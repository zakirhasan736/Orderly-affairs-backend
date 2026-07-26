# app/ai/gemini_generate.py

import logging
import time
from typing import Any

from google.genai import types
from google.genai.errors import APIError, ClientError, ServerError

from app.ai.gemini_client import get_gemini_client, get_gemini_model_candidates

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 503, 504}
MAX_GEMINI_ATTEMPTS_PER_MODEL = 1
INITIAL_BACKOFF_SECONDS = 0.5

# Rough public Flash rates ($ / 1M tokens). Estimates only — for ops logs.
_RATE_BY_MODEL_HINT: tuple[tuple[str, float, float], ...] = (
    ("2.0-flash", 0.10, 0.40),
    ("2.5-flash", 0.15, 0.60),
    ("3.5-flash", 1.50, 9.00),
)
_DEFAULT_INPUT_PER_M = 0.15
_DEFAULT_OUTPUT_PER_M = 0.60


class GeminiServiceUnavailableError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


def _error_status_code(error: Exception) -> int | None:
    status_code = getattr(error, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def is_model_unavailable_error(error: Exception) -> bool:
    """404 / retired model IDs — try the next candidate instead of failing hard."""
    status_code = _error_status_code(error)
    message = str(error).lower()
    if status_code == 404:
        return True
    return (
        "no longer available" in message
        or "not found" in message
        or "is not found" in message
        or "not supported" in message
    )


def is_quota_exhausted_error(error: Exception) -> bool:
    message = str(error).lower()
    return (
        "quota exceeded" in message
        or "resource_exhausted" in message
        or _error_status_code(error) == 429
    )


def is_retryable_gemini_error(error: Exception) -> bool:
    if is_quota_exhausted_error(error):
        return True

    status_code = _error_status_code(error)
    if status_code in RETRYABLE_STATUS_CODES:
        return True

    message = str(error).lower()
    return (
        "high demand" in message
        or "unavailable" in message
        or "rate limit" in message
        or "overloaded" in message
    )


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def estimate_gemini_usd(
    *,
    model: str,
    prompt_tokens: int,
    candidates_tokens: int,
    thoughts_tokens: int = 0,
) -> float:
    """Rough USD estimate; thoughts billed like output."""
    lowered = (model or "").lower()
    input_per_m, output_per_m = _DEFAULT_INPUT_PER_M, _DEFAULT_OUTPUT_PER_M
    for hint, in_rate, out_rate in _RATE_BY_MODEL_HINT:
        if hint in lowered:
            input_per_m, output_per_m = in_rate, out_rate
            break

    billed_out = max(0, candidates_tokens) + max(0, thoughts_tokens)
    return (max(0, prompt_tokens) / 1_000_000.0) * input_per_m + (
        billed_out / 1_000_000.0
    ) * output_per_m


def extract_usage_dict(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {
            "prompt_tokens": 0,
            "candidates_tokens": 0,
            "thoughts_tokens": 0,
            "total_tokens": 0,
        }
    return {
        "prompt_tokens": _as_int(getattr(usage, "prompt_token_count", None)),
        "candidates_tokens": _as_int(getattr(usage, "candidates_token_count", None)),
        "thoughts_tokens": _as_int(getattr(usage, "thoughts_token_count", None)),
        "total_tokens": _as_int(getattr(usage, "total_token_count", None)),
    }


def log_gemini_call_usage(
    *,
    model: str,
    usage: dict[str, int],
    operation: str = "generate",
    gemini_input: str = "unknown",
    file_name: str | None = None,
    extra: str | None = None,
) -> float:
    """Emit one clear cost line; return estimated USD for this call."""
    prompt = usage.get("prompt_tokens", 0)
    candidates = usage.get("candidates_tokens", 0)
    thoughts = usage.get("thoughts_tokens", 0)
    total = usage.get("total_tokens", 0) or (prompt + candidates + thoughts)
    usd = estimate_gemini_usd(
        model=model,
        prompt_tokens=prompt,
        candidates_tokens=candidates,
        thoughts_tokens=thoughts,
    )
    parts = [
        f"Gemini COST op={operation}",
        f"gemini_input={gemini_input}",
        f"model={model}",
        f"prompt={prompt}",
        f"candidates={candidates}",
        f"thoughts={thoughts}",
        f"total={total}",
        f"~usd={usd:.6f}",
    ]
    if file_name:
        parts.append(f"file={file_name}")
    if extra:
        parts.append(extra)
    logger.info(" ".join(parts))
    return usd


def generate_gemini_content(
    *,
    contents: list[Any],
    response_mime_type: str | None = None,
    response_json_schema: dict | None = None,
    temperature: float = 0,
    max_output_tokens: int = 8192,
    model: str | None = None,
    operation: str = "generate",
    gemini_input: str = "unknown",
    file_name: str | None = None,
):
    client = get_gemini_client()
    model_candidates = get_gemini_model_candidates(model)

    config_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        # Thinking tokens are billed as output. Keep off for document fill cost.
        "thinking_config": types.ThinkingConfig(thinking_budget=0),
    }

    if response_mime_type:
        config_kwargs["response_mime_type"] = response_mime_type

    if response_json_schema:
        config_kwargs["response_json_schema"] = response_json_schema

    config = types.GenerateContentConfig(**config_kwargs)

    last_error: Exception | None = None

    for model_index, resolved_model in enumerate(model_candidates):
        for attempt in range(1, MAX_GEMINI_ATTEMPTS_PER_MODEL + 1):
            try:
                response = client.models.generate_content(
                    model=resolved_model,
                    contents=contents,
                    config=config,
                )
                if model_index > 0:
                    logger.info(
                        "Gemini request succeeded using fallback %s",
                        resolved_model,
                    )
                usage = extract_usage_dict(response)
                usd = log_gemini_call_usage(
                    model=resolved_model,
                    usage=usage,
                    operation=operation,
                    gemini_input=gemini_input,
                    file_name=file_name,
                )
                # Attach for callers that want to aggregate per document.
                try:
                    response._orderly_usage = {  # type: ignore[attr-defined]
                        **usage,
                        "model": resolved_model,
                        "gemini_input": gemini_input,
                        "operation": operation,
                        "estimated_usd": usd,
                    }
                except Exception:
                    pass
                return response
            except (ServerError, APIError, ClientError) as error:
                last_error = error
                message = str(error).lower()

                # Some models reject thinking_budget=0 — retry once without it.
                if (
                    "thinking" in message
                    and config_kwargs.get("thinking_config") is not None
                ):
                    logger.warning(
                        "Model %s rejected thinking_config; retrying without it.",
                        resolved_model,
                    )
                    config_kwargs.pop("thinking_config", None)
                    config = types.GenerateContentConfig(**config_kwargs)
                    continue

                if is_model_unavailable_error(error):
                    logger.warning(
                        "Gemini model unavailable (%s) — trying next candidate.",
                        resolved_model,
                    )
                    break

                if is_quota_exhausted_error(error):
                    logger.warning(
                        "Gemini quota/rate limit on %s — switching model.",
                        resolved_model,
                    )
                    break

                if not is_retryable_gemini_error(error):
                    raise

                if attempt < MAX_GEMINI_ATTEMPTS_PER_MODEL:
                    delay = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    time.sleep(delay)
                    continue

                if model_index < len(model_candidates) - 1:
                    break

            except Exception as error:
                last_error = error
                if is_model_unavailable_error(error):
                    logger.warning(
                        "Gemini model unavailable (%s) — trying next candidate.",
                        resolved_model,
                    )
                    break

                if not is_retryable_gemini_error(error):
                    raise

                if attempt < MAX_GEMINI_ATTEMPTS_PER_MODEL:
                    delay = INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    time.sleep(delay)
                    continue

                if model_index < len(model_candidates) - 1:
                    break

    if last_error and is_retryable_gemini_error(last_error):
        raise GeminiServiceUnavailableError(
            "AI is temporarily busy. Please wait a moment and try Auto-fill again.",
        ) from last_error

    if last_error:
        raise last_error

    raise RuntimeError("Gemini request failed without a response")
