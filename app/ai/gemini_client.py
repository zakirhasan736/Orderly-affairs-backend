# app/ai/gemini_client.py

import os
from functools import lru_cache
from typing import Literal

from google import genai

DEFAULT_FLASH_MODEL = "gemini-3.5-flash"
DEFAULT_FLASH_FALLBACK_MODELS = "gemini-3.1-flash-lite,gemini-flash-latest"

GeminiModelTier = Literal["flash"]


@lru_cache(maxsize=1)
def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing")

    return genai.Client(api_key=api_key)


def get_gemini_model():
    return os.getenv("GEMINI_MODEL", DEFAULT_FLASH_MODEL)


def get_gemini_model_candidates(explicit_model: str | None = None) -> list[str]:
    if explicit_model:
        return [explicit_model]

    primary = get_gemini_model()
    fallbacks_raw = os.getenv("GEMINI_FALLBACK_MODELS", DEFAULT_FLASH_FALLBACK_MODELS)

    candidates: list[str] = []
    for model_name in [primary, *fallbacks_raw.split(",")]:
        cleaned = model_name.strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

    return candidates or [DEFAULT_FLASH_MODEL]
