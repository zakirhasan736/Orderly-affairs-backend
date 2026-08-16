# app/ai/llm_models.py
"""Central model IDs and pipeline flags for document intelligence.

Roles (do not interchange randomly):
  Sol   = primary semantic intelligence (classify, section, field mapping)
  Terra = bad-OCR vision reader (faithful text only)
  Luna  = cheap constrained extraction worker AFTER Sol mapping
  GPT-4o = secondary / legacy fallback

Override with env. Do not scatter model strings in extractors.
"""

from __future__ import annotations

import os

# Official OpenAI Chat Completions IDs (GPT-5.6 family, July 2026).
DEFAULT_SOL_MODEL = "gpt-5.6-sol"
DEFAULT_TERRA_MODEL = "gpt-5.6-terra"
DEFAULT_LUNA_MODEL = "gpt-5.6-luna"
DEFAULT_LEGACY_MODEL = "gpt-4o"
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


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 32) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, minimum: float = 1.0) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, value)


def pipeline_version() -> str:
    """legacy = GPT-4o only. multi_model = Sol/Terra with GPT-4o fallback."""
    raw = (os.getenv("DOCUMENT_AI_PIPELINE_VERSION") or "multi_model").strip().lower()
    if raw in {"legacy", "gpt4o", "gpt-4o"}:
        return "legacy"
    return "multi_model"


def enable_sol() -> bool:
    if pipeline_version() == "legacy":
        return False
    return _env_bool("ENABLE_SOL", True)


def enable_terra() -> bool:
    if pipeline_version() == "legacy":
        return False
    return _env_bool("ENABLE_TERRA", True)


def enable_luna() -> bool:
    if pipeline_version() == "legacy":
        return False
    return _env_bool("ENABLE_LUNA", True)


def enable_gpt4o() -> bool:
    return _env_bool("ENABLE_GPT4O", True)


def enable_luna_simple_document_routing() -> bool:
    """Keep disabled until production benchmarks justify Luna-first simple docs."""
    return _env_bool("ENABLE_LUNA_SIMPLE_DOCUMENT_ROUTING", False)


def reasoning_model() -> str:
    """Primary text semantic model (Sol, or GPT-4o in legacy mode)."""
    if not enable_sol():
        return legacy_model()
    return _env_model(
        "DOCUMENT_SEMANTIC_MODEL",
        "DOCUMENT_REASONING_MODEL",
        "OPENAI_MODEL_SOL",
        "OPENAI_MODEL",
        default=DEFAULT_SOL_MODEL,
    )


def vision_fallback_model() -> str:
    """Primary vision reader (Terra, or GPT-4o in legacy mode)."""
    if not enable_terra():
        return legacy_model()
    return _env_model(
        "DOCUMENT_VISION_MODEL",
        "DOCUMENT_VISION_FALLBACK_MODEL",
        "OPENAI_MODEL_TERRA",
        default=DEFAULT_TERRA_MODEL,
    )


def simple_extraction_model() -> str:
    """Luna — constrained extraction after Sol mapping (not primary semantics)."""
    return _env_model(
        "DOCUMENT_SIMPLE_EXTRACTION_MODEL",
        "OPENAI_MODEL_LUNA",
        default=DEFAULT_LUNA_MODEL,
    )


def legacy_model() -> str:
    """GPT-4o — secondary / legacy fallback."""
    return _env_model(
        "DOCUMENT_LEGACY_MODEL",
        "OPENAI_MODEL_GPT4O",
        default=DEFAULT_LEGACY_MODEL,
    )


def max_retries() -> int:
    return _env_int("AI_MAX_RETRIES", 3, minimum=1, maximum=8)


def sol_max_concurrency() -> int:
    return _env_int("SOL_MAX_CONCURRENCY", 4, minimum=1, maximum=16)


def terra_max_concurrency() -> int:
    return _env_int("TERRA_MAX_CONCURRENCY", 2, minimum=1, maximum=8)


def luna_max_concurrency() -> int:
    return _env_int("LUNA_MAX_CONCURRENCY", 4, minimum=1, maximum=16)


def gpt4o_max_concurrency() -> int:
    return _env_int("GPT4O_MAX_CONCURRENCY", 4, minimum=1, maximum=16)


def sol_timeout_seconds() -> float:
    """Wait for Sol. Latency is not failure — do not use a short timeout."""
    return _env_float("AI_SOL_TIMEOUT_SECONDS", 180.0, minimum=30.0)


def vision_timeout_seconds() -> float:
    return _env_float("AI_VISION_TIMEOUT_SECONDS", 180.0, minimum=30.0)


def role_concurrency(role: str) -> int:
    key = (role or "sol").strip().lower()
    if key == "terra":
        return terra_max_concurrency()
    if key == "luna":
        return luna_max_concurrency()
    if key in {"gpt4o", "gpt-4o", "legacy"}:
        return gpt4o_max_concurrency()
    return sol_max_concurrency()


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
    if "gpt-4o" in key:
        return MODEL_RATE_HINTS["gpt-4o"]
    return MODEL_RATE_HINTS["gpt-4o-mini"]


def uses_max_completion_tokens(model: str) -> bool:
    key = (model or "").strip().lower()
    return key.startswith("gpt-5") or "o1" in key or "o3" in key or "o4" in key
