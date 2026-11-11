"""Sends every LLM request to the configured provider (Gemini or Groq)."""

from __future__ import annotations
from typing import Optional, Tuple

from grader.config import SystemConfig


def call_llm(
    prompt: str,
    json_mode: bool = False,
    temperature: float = 0.2,
    max_output_tokens: int = 8192,
    timeout_sec: float = 90.0,
    model: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Returns (text, error, model_used) from the active provider."""
    provider = SystemConfig.get_llm_provider()
    key = SystemConfig.get_llm_api_key()
    if not key:
        return None, f"No {provider.title()} API key configured.", None
    if provider == "groq":
        from grader.groq_client import call_groq
        return call_groq(prompt, api_key=key, model=model or SystemConfig.get_llm_model(), json_mode=json_mode,
                         temperature=temperature, max_output_tokens=min(max_output_tokens, 4000), timeout_sec=timeout_sec)
    from grader.gemini import call_gemini
    return call_gemini(prompt, api_key=key, model=model or SystemConfig.get_llm_model(), json_mode=json_mode,
                       temperature=temperature, max_output_tokens=max_output_tokens, timeout_sec=timeout_sec)


def context_budget_chars() -> int:
    """How much student source fits in one prompt for the active provider."""
    return 7000 if SystemConfig.get_llm_provider() == "groq" else 20000
