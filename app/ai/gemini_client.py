# app/ai/gemini_client.py

import os
from functools import lru_cache
from typing import Literal

from google import genai

# Cost-first primary for 40–50 docs/user; Lite then Pro when Flash is limited.
# Prefer aliases / Gemini 3.x — gemini-2.5-flash returns 404 for many new API keys.
DEFAULT_PRIMARY_MODEL = "gemini-flash-latest"
DEFAULT_FALLBACK_MODELS = (
    "gemini-3.6-flash,gemini-flash-lite-latest,gemini-pro-latest"
)

GeminiModelTier = Literal["pro", "flash"]


@lru_cache(maxsize=1)
def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing")

    return genai.Client(api_key=api_key)


def get_gemini_model():
    return os.getenv("GEMINI_MODEL", DEFAULT_PRIMARY_MODEL)


def get_gemini_model_candidates(explicit_model: str | None = None) -> list[str]:
    if explicit_model:
        return [explicit_model]

    primary = get_gemini_model()
    fallbacks_raw = os.getenv(
        "GEMINI_FALLBACK_MODELS",
        DEFAULT_FALLBACK_MODELS,
    )

    candidates: list[str] = []
    for model_name in [primary, *fallbacks_raw.split(",")]:
        cleaned = model_name.strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

    return candidates or [DEFAULT_PRIMARY_MODEL]
