"""
Shared Google Gemini REST client used by the reviewer, test generator, driver synthesizer and file mapper.
Handles model fallbacks, transient-error retries, and reports *why* a call failed.
"""

from __future__ import annotations
import threading
import time
from typing import Dict, List, Optional, Tuple

import requests

from grader.config import SystemConfig

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.6-flash"
# Measured on this key: 3.6-flash ~5 s, 3.5-flash ~9 s, flash-lite-latest ~2 s. (3.7/3.8-flash hung past 60 s.)
FALLBACK_MODELS = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-lite-latest"]
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Models that just timed out or do not exist are skipped for a while instead of costing a full
# timeout on every single review/driver call of a batch.
_MODEL_COOLDOWN_SEC = 600.0
_model_unavailable_until: Dict[str, float] = {}
_cooldown_lock = threading.Lock()


def _mark_unavailable(model: str, seconds: float = _MODEL_COOLDOWN_SEC) -> None:
    with _cooldown_lock:
        _model_unavailable_until[model] = time.time() + seconds


def _models_to_try(primary: str) -> List[str]:
    seen = set()
    ordered = [m for m in [primary, *FALLBACK_MODELS] if m and not (m in seen or seen.add(m))]
    now = time.time()
    with _cooldown_lock:
        available = [m for m in ordered if _model_unavailable_until.get(m, 0) <= now]
    return available or ordered


def list_models(api_key: Optional[str] = None) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """Lists Gemini text models this key can call with generateContent."""
    api_key = api_key or SystemConfig.get_gemini_api_key()
    if not api_key:
        return [], "No Gemini API key configured."
    try:
        resp = requests.get(BASE_URL, params={"pageSize": 200}, headers={"x-goog-api-key": api_key}, timeout=20)
    except requests.RequestException as e:
        return [], f"network error ({type(e).__name__})"
    if resp.status_code != 200:
        return [], f"HTTP {resp.status_code} {_error_detail(resp)}"
    skip = ("tts", "image", "banana", "robotics", "computer-use", "transcribe", "embedding", "customtools")
    models = []
    for m in resp.json().get("models", []):
        name = m.get("name", "").replace("models/", "")
        if "generateContent" in m.get("supportedGenerationMethods", []) and name.startswith("gemini") and not any(s in name for s in skip):
            models.append({"id": name, "label": m.get("displayName") or name})
    return models, None


def call_gemini(
    prompt: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    json_mode: bool = False,
    temperature: float = 0.2,
    max_output_tokens: int = 8192,
    timeout_sec: float = 90.0,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Sends a single-turn prompt to Gemini.
    Returns (text, error_message, model_used). Exactly one of text / error_message is set.
    """
    api_key = api_key or SystemConfig.get_gemini_api_key()
    if not api_key:
        return None, "No Gemini API key configured.", None

    generation_config = {
        "temperature": temperature,
        "topP": 0.9,
        # Thinking models spend output tokens on reasoning first, so keep this generous
        "maxOutputTokens": max_output_tokens,
    }
    if json_mode:
        generation_config["responseMimeType"] = "application/json"

    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": generation_config}
    # Key goes in a header, never in the URL, so it cannot leak into logs or error messages
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}

    errors: List[str] = []
    for m in _models_to_try(model or SystemConfig.get_gemini_model()):
        for attempt in range(2):
            try:
                resp = requests.post(f"{BASE_URL}/{m}:generateContent", headers=headers, json=payload, timeout=timeout_sec)
            except requests.Timeout:
                errors.append(f"{m}: timed out after {timeout_sec:.0f}s")
                _mark_unavailable(m)
                break
            except requests.RequestException as e:
                errors.append(f"{m}: network error ({type(e).__name__})")
                break

            if resp.status_code == 200:
                text, why = _extract_text(resp)
                if text:
                    return text, None, m
                errors.append(f"{m}: {why}")
                break

            detail = _error_detail(resp)
            if resp.status_code in (400, 401, 403) and "API key" in detail:
                # A bad key fails the same way for every model, so stop early
                return None, f"Gemini rejected the API key ({resp.status_code}): {detail}", m
            errors.append(f"{m}: HTTP {resp.status_code} {detail}")
            if resp.status_code == 404:
                _mark_unavailable(m, 24 * 3600)  # retired / unknown model
            if resp.status_code in RETRYABLE_STATUS and attempt == 0:
                time.sleep(2.0)
                continue
            break

    return None, "; ".join(errors) or "Gemini unavailable.", None


def _extract_text(resp: requests.Response) -> Tuple[Optional[str], str]:
    try:
        data = resp.json()
    except ValueError:
        return None, "invalid JSON from API"
    candidates = data.get("candidates") or []
    if not candidates:
        block = (data.get("promptFeedback") or {}).get("blockReason")
        return None, f"no candidates returned{f' (blocked: {block})' if block else ''}"
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    if text.strip():
        return text, ""
    return None, f"empty response (finishReason={cand.get('finishReason', 'unknown')})"


def _error_detail(resp: requests.Response) -> str:
    try:
        msg = (resp.json().get("error") or {}).get("message", "")
    except ValueError:
        msg = resp.text
    return (msg or "").strip().replace("\n", " ")[:200]
