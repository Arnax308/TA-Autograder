"""
Groq chat-completions client (OpenAI-compatible API).

Groq's free tier allows ~8k tokens per minute per model, and the request's prompt plus max_tokens
must fit inside that. A small in-process limiter paces requests so parallel graders wait their
turn instead of failing, and a model that hits its daily cap is skipped for the rest of the day.
"""

from __future__ import annotations
import os
import re
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import requests

BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"
FALLBACK_MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
TPM_LIMIT = int(os.getenv("GROQ_TPM_LIMIT", "8000"))

_lock = threading.Lock()
_usage: Dict[str, Deque[Tuple[float, int]]] = {}
_exhausted_until: Dict[str, float] = {}


def estimate_tokens(text: str) -> int:
    # Source code tokenises at roughly 3 characters per token
    return int(len(text) / 3.0) + 50


def _reserve(model: str, tokens: int) -> None:
    """Blocks until `tokens` fit in the model's rolling one-minute budget, then records them."""
    tokens = min(tokens, TPM_LIMIT)
    while True:
        with _lock:
            window = _usage.setdefault(model, deque())
            now = time.time()
            while window and now - window[0][0] > 60:
                window.popleft()
            used = sum(t for _, t in window)
            if used + tokens <= TPM_LIMIT:
                window.append((now, tokens))
                return
            wait = 60 - (now - window[0][0]) + 0.5
        time.sleep(max(0.5, min(wait, 60)))


def _models(primary: Optional[str]) -> List[str]:
    """Available models, least-used first, so parallel graders spread across both per-minute budgets."""
    seen = set()
    ordered = [m for m in [primary or DEFAULT_MODEL, *FALLBACK_MODELS] if m and not (m in seen or seen.add(m))]
    now = time.time()
    available = [m for m in ordered if _exhausted_until.get(m, 0) <= now] or ordered[:1]
    with _lock:
        def used(m):
            return sum(t for ts, t in _usage.get(m, ()) if now - ts <= 60)
        # The small gpt-oss-20b is only a backup for when the stronger models hit their daily limit
        return sorted(available, key=lambda m: (m.endswith("-20b") and m != primary, used(m), ordered.index(m)))


def call_groq(
    prompt: str,
    api_key: str,
    model: Optional[str] = None,
    json_mode: bool = False,
    temperature: float = 0.2,
    max_output_tokens: int = 3000,
    timeout_sec: float = 120.0,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Returns (text, error, model_used)."""
    errors: List[str] = []
    prompt_tokens = estimate_tokens(prompt)
    # Keep the request inside the per-minute budget, which Groq checks against prompt + max_tokens
    max_output_tokens = max(256, min(max_output_tokens, TPM_LIMIT - prompt_tokens - 200))
    if prompt_tokens + 256 > TPM_LIMIT:
        return None, f"Prompt too large for Groq's {TPM_LIMIT} tokens/minute limit (~{prompt_tokens} tokens).", None

    for m in _models(model):
        body = {
            "model": m,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if "gpt-oss" in m:
            body["reasoning_effort"] = "low"
        elif "qwen" in m:
            body["reasoning_format"] = "hidden"

        for attempt in range(4):
            _reserve(m, prompt_tokens + max_output_tokens)
            try:
                resp = requests.post(f"{BASE_URL}/chat/completions", json=body, timeout=timeout_sec,
                                     headers={"Authorization": f"Bearer {api_key}"})
            except requests.Timeout:
                errors.append(f"{m}: timed out")
                break
            except requests.RequestException as e:
                errors.append(f"{m}: network error ({type(e).__name__})")
                break

            if resp.status_code == 200:
                try:
                    choice = resp.json()["choices"][0]
                    text = (choice.get("message") or {}).get("content") or ""
                except (ValueError, KeyError, IndexError):
                    text, choice = "", {}
                if text.strip():
                    return text, None, m
                errors.append(f"{m}: empty response (finish_reason={choice.get('finish_reason')})")
                break

            detail = _detail(resp)
            if resp.status_code == 401:
                return None, f"Groq rejected the API key: {detail}", m
            if resp.status_code == 429:
                if re.search(r"per day|TPD|RPD", detail, re.IGNORECASE):
                    _exhausted_until[m] = time.time() + 3600 * 6
                    errors.append(f"{m}: daily limit reached")
                    break
                retry = resp.headers.get("retry-after")
                try:
                    delay = float(retry) if retry else 10.0
                except ValueError:
                    delay = 10.0
                time.sleep(min(max(delay, 1.0), 65.0))
                continue
            if resp.status_code in (500, 502, 503) and attempt < 2:
                time.sleep(3)
                continue
            errors.append(f"{m}: HTTP {resp.status_code} {detail}")
            break

    return None, "; ".join(errors) or "Groq unavailable.", None


def list_groq_models(api_key: str) -> Tuple[List[Dict[str, str]], Optional[str]]:
    try:
        resp = requests.get(f"{BASE_URL}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=20)
    except requests.RequestException as e:
        return [], f"network error ({type(e).__name__})"
    if resp.status_code != 200:
        return [], f"HTTP {resp.status_code} {_detail(resp)}"
    skip = ("whisper", "orpheus", "guard", "safeguard", "allam")
    models = [{"id": m["id"], "label": m["id"]} for m in resp.json().get("data", [])
              if m.get("active", True) and not any(s in m["id"] for s in skip)]
    return sorted(models, key=lambda x: x["id"]), None


def _detail(resp: requests.Response) -> str:
    try:
        msg = (resp.json().get("error") or {}).get("message", "")
    except ValueError:
        msg = resp.text
    return (msg or "").strip().replace("\n", " ")[:220]
