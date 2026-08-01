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
            "Weak OCR on images/PDF falls back to GPT vision. "
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


def contents_to_openai_user_content(contents: list[Any]) -> str | list[dict[str, Any]]:
    """
    Convert mixed text + image content parts into OpenAI chat user content.
    Returns a plain string when there are no images (cheaper / simpler).
    """
    text_parts: list[str] = []
    image_parts: list[dict[str, Any]] = []

    for item in contents:
        if isinstance(item, str):
            if item.strip():
                text_parts.append(item)
            continue

        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            text_parts.append(text)
            continue

        if isinstance(item, dict):
            if item.get("text") and item.get("type") not in {"image", "image_url"}:
                text_parts.append(str(item["text"]))
                continue

            if item.get("type") == "image" and item.get("data_b64"):
                mime = str(item.get("mime_type") or "image/png")
                if mime == "image/jpg":
                    mime = "image/jpeg"
                image_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{item['data_b64']}",
                        },
                    }
                )
                continue

            if item.get("type") == "image_url" and item.get("image_url"):
                image_parts.append(
                    {
                        "type": "image_url",
                        "image_url": item["image_url"],
                    }
                )
                continue

        raise RuntimeError(
            "Fill brain received unsupported content part. "
            "OCR/vision pipeline must provide text or image parts."
        )

    if not text_parts and not image_parts:
        raise RuntimeError("Empty prompt for fill brain")

    if not image_parts:
        return "\n\n".join(text_parts)

    user_content: list[dict[str, Any]] = []
    for text in text_parts:
        user_content.append({"type": "text", "text": text})
    user_content.extend(image_parts)
    return user_content


def contents_to_text_prompt(contents: list[Any]) -> str:
    """Legacy text-only join."""
    content = contents_to_openai_user_content(contents)
    if isinstance(content, str):
        return content
    texts = [
        part.get("text", "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ]
    joined = "\n\n".join(t for t in texts if t.strip())
    if not joined.strip():
        raise RuntimeError("Empty prompt for fill brain")
    return joined


def build_system_prompt(*, vision: bool = False) -> str:
    base = (
        "You are the Orderly Affairs document fill brain. "
        "Map values into the exact Orderly section field keys. "
        "Prefer dedicated fields over notes. "
        "Do not invent facts that are not in the document. "
        "Do not return passwords, full SSN, or full card numbers. "
        "Reply with valid JSON only."
    )
    if vision:
        return (
            base
            + " Read the attached document image(s) carefully, including small print. "
            "If OCR text is also provided, treat the image as the source of truth."
        )
    return base + " Read ONLY the provided document text."


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
    del response_mime_type  # OpenAI uses response_format json_object

    if gemini_input is not None:
        llm_input = gemini_input

    provider, resolved_model = resolve_provider_and_model(explicit_model=model)

    user_content = contents_to_openai_user_content(contents)
    has_images = not isinstance(user_content, str)
    if llm_input == "file_bytes":
        llm_input = "vision"
    if has_images:
        llm_input = "vision"

    prompt_for_log = (
        user_content
        if isinstance(user_content, str)
        else "\n\n".join(
            part.get("text", "")
            for part in user_content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    )

    if response_json_schema:
        schema_note = (
            "\n\nReturn ONLY valid JSON matching this schema intent "
            "(keys and shapes). Do not wrap in markdown.\n"
            f"SCHEMA:\n{json.dumps(response_json_schema)[:12000]}"
        )
        if isinstance(user_content, str):
            user_content = f"{user_content}{schema_note}"
            prompt_for_log = user_content
        else:
            user_content = list(user_content) + [
                {"type": "text", "text": schema_note}
            ]
            prompt_for_log = f"{prompt_for_log}{schema_note}"

    system_prompt = build_system_prompt(vision=has_images)
    url = f"{_base_url(provider)}/chat/completions"
    body: dict[str, Any] = {
        "model": resolved_model,
        "temperature": temperature,
        "max_tokens": max_output_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        resp = requests.post(
            url,
            headers=_auth_headers(provider),
            json=body,
            timeout=180 if has_images else 120,
        )
    except requests.RequestException as error:
        raise RuntimeError(f"{provider} request failed: {error}") from error

    if resp.status_code >= 400:
        detail = resp.text[:500]
        if resp.status_code in {429, 500, 503, 504}:
            raise LLMServiceUnavailableError(
                "Our AI is finishing other documents right now. Please wait about a minute, then try again. Your upload is saved — nothing is wrong with your file.",
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
        "user_prompt": (prompt_for_log or "")[:50000],
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
