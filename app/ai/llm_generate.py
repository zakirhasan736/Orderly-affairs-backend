# app/ai/llm_generate.py
"""Orderly fill brain — OpenAI gpt-4o-mini (or future own model). No Gemini."""

from __future__ import annotations

import json
import logging
import os
from types import SimpleNamespace
from typing import Any

import requests

from app.ai.llm_context import get_llm_settings

logger = logging.getLogger(__name__)

PROVIDERS = ("openai", "own")
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_OWN_MODEL = "orderly-fill-v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"

_RATE_HINTS: dict[str, tuple[float, float]] = {
    "openai": (0.15, 0.60),
    "own": (0.0, 0.0),
}


class LLMServiceUnavailableError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


# Back-compat alias used by older imports during transition.
GeminiServiceUnavailableError = LLMServiceUnavailableError


def active_brain_info() -> dict[str, Any]:
    provider, model = resolve_provider_and_model()
    configured = (
        bool((os.getenv("OPENAI_API_KEY") or "").strip())
        if provider == "openai"
        else bool((os.getenv("OWN_MODEL_BASE_URL") or "").strip())
    )
    return {
        "provider": provider,
        "model": model,
        "configured": configured,
        "mode": (
            "openai_gpt4o_mini"
            if provider == "openai"
            else "own_model_openai_compatible"
        ),
        "notes": (
            "Local OCR/PDF text → gpt-4o-mini JSON fill. "
            "Successful fills are stored as Orderly skill training JSON."
        ),
    }


def resolve_provider_and_model(
    *,
    explicit_model: str | None = None,
) -> tuple[str, str]:
    ctx = get_llm_settings()
    provider = (
        str(ctx.get("provider") or os.getenv("AI_PROVIDER") or "openai")
        .strip()
        .lower()
    )
    if provider not in PROVIDERS:
        provider = "openai"

    if provider == "own":
        model = (
            (explicit_model or "").strip()
            or str(ctx.get("model") or "").strip()
            or (os.getenv("OWN_MODEL_NAME") or DEFAULT_OWN_MODEL).strip()
            or DEFAULT_OWN_MODEL
        )
        return "own", model

    model = (
        (explicit_model or "").strip()
        or str(ctx.get("model") or "").strip()
        or (os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL).strip()
        or DEFAULT_OPENAI_MODEL
    )
    return "openai", model


def contents_to_text_prompt(contents: list[Any]) -> str:
    parts: list[str] = []
    for item in contents:
        if isinstance(item, str):
            parts.append(item)
            continue
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text)
            continue
        if isinstance(item, dict) and item.get("text"):
            parts.append(str(item["text"]))
            continue
        raise RuntimeError(
            "Fill brain is text-only. Local OCR must provide document text."
        )
    joined = "\n\n".join(p for p in parts if p and str(p).strip())
    if not joined.strip():
        raise RuntimeError("Empty prompt for fill brain")
    return joined


def _base_url(provider: str) -> str:
    if provider == "own":
        base = (os.getenv("OWN_MODEL_BASE_URL") or "").strip()
        if not base:
            raise RuntimeError("OWN_MODEL_BASE_URL is missing")
        return base.rstrip("/")
    return OPENAI_BASE_URL


def _auth_headers(provider: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if provider == "own":
        key = (os.getenv("OWN_MODEL_API_KEY") or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is missing")
    headers["Authorization"] = f"Bearer {key}"
    return headers


def estimate_llm_usd(
    *,
    provider: str,
    prompt_tokens: int,
    candidates_tokens: int,
) -> float:
    in_rate, out_rate = _RATE_HINTS.get(provider, (0.15, 0.60))
    return (max(0, prompt_tokens) / 1_000_000.0) * in_rate + (
        max(0, candidates_tokens) / 1_000_000.0
    ) * out_rate


def log_llm_call_usage(
    *,
    model: str,
    usage: dict[str, int],
    operation: str = "generate",
    llm_input: str = "text",
    file_name: str | None = None,
    provider: str = "openai",
    extra: str | None = None,
) -> float:
    prompt = int(usage.get("prompt_tokens") or 0)
    candidates = int(usage.get("candidates_tokens") or 0)
    thoughts = int(usage.get("thoughts_tokens") or 0)
    total = int(usage.get("total_tokens") or 0) or (prompt + candidates + thoughts)
    usd = estimate_llm_usd(
        provider=provider,
        prompt_tokens=prompt,
        candidates_tokens=candidates + thoughts,
    )
    parts = [
        f"LLM COST op={operation}",
        f"llm_input={llm_input}",
        f"provider={provider}",
        f"model={model}",
        f"prompt={prompt}",
        f"candidates={candidates}",
        f"total={total}",
        f"~usd={usd:.6f}",
    ]
    if file_name:
        parts.append(f"file={file_name}")
    if extra:
        parts.append(extra)
    logger.info(" ".join(parts))
    return usd


def build_system_prompt() -> str:
    return (
        "You are the Orderly Affairs document fill brain. "
        "Read ONLY the provided document text. "
        "Map values into the exact Orderly section field keys. "
        "Prefer dedicated fields over notes. "
        "Do not invent facts that are not in the text. "
        "Do not return passwords, full SSN, or full card numbers. "
        "Reply with valid JSON only."
    )


def generate_llm_content(
    *,
    contents: list[Any],
    response_mime_type: str | None = None,
    response_json_schema: dict | None = None,
    temperature: float = 0,
    max_output_tokens: int = 8192,
    model: str | None = None,
    operation: str = "generate",
    llm_input: str = "text",
    file_name: str | None = None,
    # Legacy kwarg names from Gemini era
    gemini_input: str | None = None,
):
    if gemini_input is not None:
        llm_input = gemini_input

    provider, resolved_model = resolve_provider_and_model(explicit_model=model)
    if llm_input == "file_bytes":
        raise RuntimeError("Fill brain is text-only. OCR the document first.")

    prompt = contents_to_text_prompt(contents)
    if response_json_schema:
        prompt = (
            f"{prompt}\n\n"
            "Return ONLY valid JSON matching this schema intent "
            "(keys and shapes). Do not wrap in markdown.\n"
            f"SCHEMA:\n{json.dumps(response_json_schema)[:12000]}"
        )

    system_prompt = build_system_prompt()
    url = f"{_base_url(provider)}/chat/completions"
    body: dict[str, Any] = {
        "model": resolved_model,
        "temperature": temperature,
        "max_tokens": max_output_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        resp = requests.post(
            url,
            headers=_auth_headers(provider),
            json=body,
            timeout=120,
        )
    except requests.RequestException as error:
        raise RuntimeError(f"{provider} request failed: {error}") from error

    if resp.status_code >= 400:
        detail = resp.text[:500]
        if resp.status_code in {429, 500, 503, 504}:
            raise LLMServiceUnavailableError(
                "AI is temporarily busy. Please wait a moment and try Auto-fill again.",
                status_code=resp.status_code,
            )
        raise RuntimeError(f"{provider} error {resp.status_code}: {detail}")

    data = resp.json()
    choices = data.get("choices") or []
    text = ""
    if choices:
        message = (choices[0].get("message") or {})
        text = message.get("content") or ""

    usage_raw = data.get("usage") or {}
    usage = {
        "prompt_tokens": int(usage_raw.get("prompt_tokens") or 0),
        "candidates_tokens": int(usage_raw.get("completion_tokens") or 0),
        "thoughts_tokens": 0,
        "total_tokens": int(usage_raw.get("total_tokens") or 0),
    }
    usd = log_llm_call_usage(
        model=resolved_model,
        usage=usage,
        operation=operation,
        llm_input=llm_input,
        file_name=file_name,
        provider=provider,
    )

    response = SimpleNamespace(
        text=text,
        candidates=[],
        usage_metadata=SimpleNamespace(
            prompt_token_count=usage["prompt_tokens"],
            candidates_token_count=usage["candidates_tokens"],
            thoughts_token_count=0,
            total_token_count=usage["total_tokens"]
            or (usage["prompt_tokens"] + usage["candidates_tokens"]),
        ),
    )
    response._orderly_usage = {
        **usage,
        "model": resolved_model,
        "provider": provider,
        "llm_input": llm_input,
        "operation": operation,
        "estimated_usd": usd,
        "system_prompt": system_prompt,
        "user_prompt": prompt[:50000],
    }
    return response


# Compatibility wrappers for existing call sites.
def generate_gemini_content(**kwargs):
    return generate_llm_content(**kwargs)


def is_quota_exhausted_error(error: Exception) -> bool:
    message = str(error).lower()
    status = getattr(error, "status_code", None)
    return (
        "quota exceeded" in message
        or "resource_exhausted" in message
        or "rate limit" in message
        or status == 429
    )
