# app/ai/llm_generate.py
"""Orderly fill brain — Sol (semantics) + Terra (vision) + GPT-4o/Luna fallback."""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from types import SimpleNamespace
from typing import Any, Literal

import requests

from app.ai.llm_context import get_llm_settings
from app.ai.llm_models import (
    DEFAULT_OPENAI_MODEL,
    DEFAULT_SOL_MODEL,
    DEFAULT_TERRA_MODEL,
    enable_gpt4o,
    enable_luna,
    enable_sol,
    enable_terra,
    legacy_model,
    max_retries,
    model_rate_hint,
    reasoning_model,
    role_concurrency,
    simple_extraction_model,
    sol_timeout_seconds,
    uses_max_completion_tokens,
    vision_fallback_model,
    vision_timeout_seconds,
)

# Re-exported for llm_providers / older imports.
assert DEFAULT_OPENAI_MODEL and DEFAULT_TERRA_MODEL

logger = logging.getLogger(__name__)

PROVIDERS = ("openai", "own")
DEFAULT_OWN_MODEL = "orderly-fill-v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"

LLMRole = Literal["sol", "terra", "luna", "gpt4o"]

_RATE_HINTS: dict[str, tuple[float, float]] = {
    "openai": (5.0, 30.0),
    "own": (0.0, 0.0),
}

# Shown to clients only after retries/fallbacks are exhausted. Never "busy".
USER_SAFE_FAIL_MESSAGE = (
    "We couldn't finish reading that document. Please try again in a moment."
)
USER_SAFE_WAIT_MESSAGE = "Working on this document…"

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_FALLBACK_STATUS = {400, 404, 429, 500, 502, 503, 504}
_SEMAPHORES: dict[str, threading.Semaphore] = {}
_SEMAPHORE_LOCK = threading.Lock()
PIPELINE_METRICS: dict[str, int] = {
    "sol_calls": 0,
    "terra_calls": 0,
    "luna_calls": 0,
    "gpt4o_calls": 0,
    "retries": 0,
    "sol_fallback_gpt4o": 0,
    "sol_fallback_luna": 0,
    "terra_fallback_gpt4o": 0,
}


class LLMServiceUnavailableError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message or USER_SAFE_FAIL_MESSAGE)
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
        "sol_model": reasoning_model() if provider == "openai" else model,
        "terra_model": vision_fallback_model() if provider == "openai" else model,
        "luna_model": simple_extraction_model() if provider == "openai" else model,
        "legacy_model": legacy_model() if provider == "openai" else model,
        "mode": (
            "openai_gpt56_sol_terra"
            if provider == "openai"
            else "own_model_openai_compatible"
        ),
        "notes": (
            "Local OCR/PDF text → semantic JSON fill. "
            "Weak OCR pages are read by vision, then mapped to vault fields. "
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
        or reasoning_model()
        or DEFAULT_SOL_MODEL
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
    model: str | None = None,
) -> float:
    if model:
        in_rate, out_rate = model_rate_hint(model)
    else:
        in_rate, out_rate = _RATE_HINTS.get(provider, (5.0, 30.0))
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
        model=model,
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


SOL_SYSTEM_PROMPT = (
    "You are the Orderly Affairs document intelligence engine. "
    "You receive prepared document TEXT (from OCR or a vision reader), never raw pixels. "
    "Understand the document as a professional would: topic, section, labels, and values. "
    "Match by meaning, not exact wording. Misspelled labels (Polcy Numbor) still map to the "
    "correct schema field, but VALUES must stay evidence-based — never invent or guess. "
    "If a value is not clearly supported, return null. "
    "Do not return passwords, full SSN, or full card numbers. "
    "Reply with valid JSON only using the exact Orderly field keys."
)

TERRA_SYSTEM_PROMPT = (
    "You are a faithful visual document reader. "
    "Your ONLY job is to reconstruct clean text from the attached page image(s). "
    "Do not classify the document. Do not pick application sections. "
    "Do not map database fields. Do not auto-fill. Do not invent missing information. "
    "Preserve headings, labels, values, line breaks, tables, page markers, and checkbox states. "
    "If a character is visually ambiguous (O/0, I/1, S/5), keep it and mark uncertainty "
    "inline like [O/0]. Return JSON only: "
    '{"text":"...","uncertain_spans":[],"notes":""}.'
)

LUNA_SYSTEM_PROMPT = (
    "You are a constrained extraction worker. "
    "Extract ONLY the values for the approved mapping plan and source text. "
    "Do not reinterpret the application schema. Do not invent missing values. "
    "If a mapped field has no supporting evidence, return null. JSON only."
)


def build_system_prompt(*, vision: bool = False, role: LLMRole = "sol") -> str:
    if role == "terra" or vision:
        return TERRA_SYSTEM_PROMPT
    if role == "luna":
        return LUNA_SYSTEM_PROMPT
    return SOL_SYSTEM_PROMPT + " Read ONLY the provided document text."


def reset_pipeline_metrics() -> None:
    for key in PIPELINE_METRICS:
        PIPELINE_METRICS[key] = 0


def _metric(key: str, amount: int = 1) -> None:
    PIPELINE_METRICS[key] = PIPELINE_METRICS.get(key, 0) + amount


def _semaphore_for(role: str) -> threading.Semaphore:
    key = (role or "sol").strip().lower()
    with _SEMAPHORE_LOCK:
        existing = _SEMAPHORES.get(key)
        if existing is not None:
            return existing
        sem = threading.Semaphore(role_concurrency(key))
        _SEMAPHORES[key] = sem
        return sem


def reset_role_semaphores() -> None:
    """Test helper — drop cached semaphores so env concurrency is re-read."""
    with _SEMAPHORE_LOCK:
        _SEMAPHORES.clear()


def _sleep_backoff(attempt: int, retry_after: float | None = None) -> None:
    if retry_after is not None and retry_after > 0:
        delay = min(float(retry_after), 20.0)
    else:
        delay = min(8.0, (2 ** max(0, attempt)) * 0.6)
    delay += random.uniform(0.05, 0.45)
    time.sleep(delay)


def _retry_after_seconds(resp: requests.Response) -> float | None:
    raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _role_for_model(model: str, default: LLMRole) -> LLMRole:
    key = (model or "").strip().lower()
    if "terra" in key:
        return "terra"
    if "luna" in key:
        return "luna"
    if "gpt-4o" in key or key == "gpt4o":
        return "gpt4o"
    if "sol" in key:
        return "sol"
    return default


def _fallback_chain(
    *,
    role: LLMRole,
    explicit_model: str | None,
    has_images: bool,
) -> list[tuple[LLMRole, str]]:
    """Ordered (role, model_id). Explicit model skips automatic fallbacks."""
    if (explicit_model or "").strip():
        chosen = explicit_model.strip()
        return [(_role_for_model(chosen, role), chosen)]

    chain: list[tuple[LLMRole, str]] = []
    if has_images or role == "terra":
        if enable_terra():
            chain.append(("terra", vision_fallback_model()))
        if enable_gpt4o():
            gpt4o = legacy_model()
            if not chain or chain[-1][1] != gpt4o:
                chain.append(("gpt4o", gpt4o))
        return chain or [("gpt4o", legacy_model())]

    if role == "luna":
        if enable_luna():
            chain.append(("luna", simple_extraction_model()))
        if enable_gpt4o():
            chain.append(("gpt4o", legacy_model()))
        return chain or [("sol", reasoning_model())]

    # Sol semantic path: Sol → GPT-4o → Luna (availability only, not latency).
    if enable_sol():
        chain.append(("sol", reasoning_model()))
    if enable_gpt4o():
        gpt4o = legacy_model()
        if not chain or chain[-1][1] != gpt4o:
            chain.append(("gpt4o", gpt4o))
    if enable_luna():
        luna = simple_extraction_model()
        if not chain or chain[-1][1] != luna:
            chain.append(("luna", luna))
    return chain or [("sol", reasoning_model())]


def _build_chat_body(
    *,
    model: str,
    system_prompt: str,
    user_content: str | list[dict[str, Any]],
    max_output_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }
    if uses_max_completion_tokens(model):
        body["max_completion_tokens"] = max_output_tokens
    else:
        body["max_tokens"] = max_output_tokens
        body["temperature"] = temperature
    return body


def _post_chat_completion(
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
    attempts: int,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        except (requests.Timeout, requests.ConnectionError) as error:
            last_error = error
            _metric("retries")
            logger.warning(
                "LLM transport retry attempt=%s timeout/connection: %s",
                attempt + 1,
                error,
            )
            if attempt + 1 >= attempts:
                break
            _sleep_backoff(attempt)
            continue
        except requests.RequestException as error:
            raise RuntimeError(f"provider request failed: {error}") from error

        if resp.status_code < 400:
            return resp

        if resp.status_code in _RETRYABLE_STATUS and attempt + 1 < attempts:
            _metric("retries")
            logger.warning(
                "LLM HTTP retry attempt=%s status=%s",
                attempt + 1,
                resp.status_code,
            )
            _sleep_backoff(attempt, _retry_after_seconds(resp))
            last_error = LLMServiceUnavailableError(
                USER_SAFE_FAIL_MESSAGE, status_code=resp.status_code
            )
            continue

        if resp.status_code in _FALLBACK_STATUS:
            raise LLMServiceUnavailableError(
                USER_SAFE_FAIL_MESSAGE, status_code=resp.status_code
            )
        raise RuntimeError(f"provider error {resp.status_code}: {resp.text[:500]}")

    if isinstance(last_error, LLMServiceUnavailableError):
        raise last_error
    raise LLMServiceUnavailableError(
        USER_SAFE_FAIL_MESSAGE, status_code=503
    ) from last_error


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
    role: LLMRole = "sol",
    allow_fallback: bool = True,
    # Legacy kwarg names from Gemini era
    gemini_input: str | None = None,
):
    del response_mime_type  # OpenAI uses response_format json_object

    if gemini_input is not None:
        llm_input = gemini_input

    user_content = contents_to_openai_user_content(contents)
    has_images = not isinstance(user_content, str)
    if llm_input == "file_bytes":
        llm_input = "vision"
    if has_images:
        llm_input = "vision"
        if role not in {"terra", "gpt4o"}:
            role = "terra"

    provider, _resolved = resolve_provider_and_model(
        explicit_model=(model or "").strip() or None
    )

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

    chain = _fallback_chain(
        role=role,
        explicit_model=model if (model and not allow_fallback) else None,
        has_images=has_images,
    )
    if not allow_fallback:
        if model:
            chain = [(_role_for_model(model, role), model)]
        else:
            chain = chain[:1]

    url = f"{_base_url(provider)}/chat/completions"
    headers = _auth_headers(provider)
    timeout = (
        vision_timeout_seconds()
        if has_images or role == "terra"
        else sol_timeout_seconds()
    )
    attempts = max_retries()
    last_error: Exception | None = None
    response = None
    resolved_model = chain[0][1] if chain else reasoning_model()
    used_role: LLMRole = role
    system_prompt = build_system_prompt(vision=has_images, role=role)

    for index, (attempt_role, attempt_model) in enumerate(chain):
        system_prompt = build_system_prompt(
            vision=has_images, role="terra" if has_images else attempt_role
        )
        body = _build_chat_body(
            model=attempt_model,
            system_prompt=system_prompt,
            user_content=user_content,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        _metric(f"{attempt_role}_calls")
        if index > 0:
            if attempt_role == "gpt4o" and role == "sol":
                _metric("sol_fallback_gpt4o")
            elif attempt_role == "luna" and role == "sol":
                _metric("sol_fallback_luna")
            elif attempt_role == "gpt4o" and role == "terra":
                _metric("terra_fallback_gpt4o")
            logger.info(
                "LLM availability fallback op=%s from_role=%s to_role=%s",
                operation,
                role,
                attempt_role,
            )

        try:
            with _semaphore_for(attempt_role):
                resp = _post_chat_completion(
                    url=url,
                    headers=headers,
                    body=body,
                    timeout=timeout,
                    attempts=attempts,
                )
        except LLMServiceUnavailableError as error:
            last_error = error
            continue

        data = resp.json()
        choices = data.get("choices") or []
        text = ""
        if choices:
            message = choices[0].get("message") or {}
            text = message.get("content") or ""

        usage_raw = data.get("usage") or {}
        usage = {
            "prompt_tokens": int(usage_raw.get("prompt_tokens") or 0),
            "candidates_tokens": int(usage_raw.get("completion_tokens") or 0),
            "thoughts_tokens": 0,
            "total_tokens": int(usage_raw.get("total_tokens") or 0),
        }
        usd = log_llm_call_usage(
            model=attempt_model,
            usage=usage,
            operation=operation,
            llm_input=llm_input,
            file_name=file_name,
            provider=provider,
            extra=f"role={attempt_role}",
        )
        resolved_model = attempt_model
        used_role = attempt_role
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
            "role": used_role,
            "estimated_usd": usd,
            "system_prompt": system_prompt,
            "user_prompt": (prompt_for_log or "")[:50000],
        }
        return response

    raise LLMServiceUnavailableError(
        USER_SAFE_FAIL_MESSAGE,
        status_code=getattr(last_error, "status_code", 503),
    ) from last_error


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
        or isinstance(error, LLMServiceUnavailableError)
    )
