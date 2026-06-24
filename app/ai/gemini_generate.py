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


class GeminiServiceUnavailableError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


def _error_status_code(error: Exception) -> int | None:
    status_code = getattr(error, "status_code", None)
    return status_code if isinstance(status_code, int) else None


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


def generate_gemini_content(
    *,
    contents: list[Any],
    response_mime_type: str | None = None,
    response_json_schema: dict | None = None,
    temperature: float = 0,
    max_output_tokens: int = 8192,
    model: str | None = None,
):
    client = get_gemini_client()
    model_candidates = get_gemini_model_candidates(model)

    config_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
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
                return response
            except (ServerError, APIError, ClientError) as error:
                last_error = error

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
