# app/ai/llm_models.py
"""Central GPT-5.6 Sol / Terra model IDs for the document fill pipeline.

Sol  = text reasoning (classify, semantic field mapping)
Terra = vision fallback reader (faithful text reconstruction only)

Override with env. Do not scatter model strings in extractors.
"""

from __future__ import annotations

import os

# Official OpenAI Chat Completions IDs (GPT-5.6 family, July 2026).
DEFAULT_SOL_MODEL = "gpt-5.6-sol"
DEFAULT_TERRA_MODEL = "gpt-5.6-terra"
# Backward-compatible alias if operators still set OPENAI_MODEL.
DEFAULT_OPENAI_MODEL = DEFAULT_SOL_MODEL

# USD per 1M tokens (input, output) — used only for operational cost logs.
MODEL_RATE_HINTS: dict[str, tuple[float, float]] = {
    "gpt-5.6-sol": (5.0, 30.0),
    "gpt-5.6": (5.0, 30.0),
    "gpt-5.6-terra": (2.5, 15.0),
    "gpt-5.6-luna": (1.0, 6.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
}


def _env_model(*names: str, default: str) -> str:
    for name in names:
        raw = (os.getenv(name) or "").strip()
        if raw:
            return raw
    return default


def reasoning_model() -> str:
    """GPT-5.6 Sol — classify + extract from prepared text."""
    return _env_model(
        "DOCUMENT_REASONING_MODEL",
        "OPENAI_MODEL_SOL",
        "OPENAI_MODEL",
        default=DEFAULT_SOL_MODEL,
    )


def vision_fallback_model() -> str:
    """GPT-5.6 Terra — read original pages only when OCR is bad."""
    return _env_model(
        "DOCUMENT_VISION_FALLBACK_MODEL",
        "OPENAI_MODEL_TERRA",
        default=DEFAULT_TERRA_MODEL,
    )


def model_rate_hint(model: str) -> tuple[float, float]:
    key = (model or "").strip().lower()
    if key in MODEL_RATE_HINTS:
        return MODEL_RATE_HINTS[key]
    if "terra" in key:
        return MODEL_RATE_HINTS["gpt-5.6-terra"]
    if "luna" in key:
        return MODEL_RATE_HINTS["gpt-5.6-luna"]
    if "sol" in key or key.startswith("gpt-5"):
        return MODEL_RATE_HINTS["gpt-5.6-sol"]
    return MODEL_RATE_HINTS["gpt-4o-mini"]


def uses_max_completion_tokens(model: str) -> bool:
    key = (model or "").strip().lower()
    return key.startswith("gpt-5") or "o1" in key or "o3" in key or "o4" in key
