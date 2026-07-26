# app/ai/gemini_client.py

import os
from functools import lru_cache
from typing import Literal

from google import genai

# Flash-only stack — keep the cheap 2.x Flash models for document fill.
# Avoid 3.5 Flash here ($1.50/$9) unless you explicitly opt in via env.
DEFAULT_PRIMARY_MODEL = "gemini-2.5-flash"
DEFAULT_FALLBACK_MODELS = "gemini-2.0-flash"

GeminiModelTier = Literal["pro", "flash"]


@lru_cache(maxsize=1)
def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing")

    return genai.Client(api_key=api_key)


def get_gemini_model():
    return os.getenv("GEMINI_MODEL", DEFAULT_PRIMARY_MODEL)


def _is_allowed_flash_model(model_name: str) -> bool:
    """Allow Gemini 2 / 2.5 / 3.5 Flash only — never Pro or Live."""
    lowered = model_name.strip().lower()
    if not lowered or "flash" not in lowered:
        return False
    blocked = ("pro", "live", "omni", "tts", "realtime", "image", "banana")
    return not any(token in lowered for token in blocked)


def get_gemini_model_candidates(explicit_model: str | None = None) -> list[str]:
    if explicit_model:
        cleaned = explicit_model.strip()
        return [cleaned] if cleaned and _is_allowed_flash_model(cleaned) else []

    primary = get_gemini_model()
    fallbacks_raw = os.getenv(
        "GEMINI_FALLBACK_MODELS",
        DEFAULT_FALLBACK_MODELS,
    )

    candidates: list[str] = []
    for model_name in [primary, *fallbacks_raw.split(",")]:
        cleaned = model_name.strip()
        if (
            cleaned
            and cleaned not in candidates
            and _is_allowed_flash_model(cleaned)
        ):
            candidates.append(cleaned)

    return candidates or [DEFAULT_PRIMARY_MODEL]
