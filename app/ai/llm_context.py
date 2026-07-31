# app/ai/llm_context.py
"""Per-request LLM brain settings (provider/model/learning) via contextvars."""

from __future__ import annotations

import contextvars
from typing import Any

_llm_settings: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "orderly_llm_settings",
    default=None,
)


def set_llm_settings(settings: dict[str, Any] | None) -> None:
    _llm_settings.set(settings)


def get_llm_settings() -> dict[str, Any]:
    return dict(_llm_settings.get() or {})


def clear_llm_settings() -> None:
    _llm_settings.set(None)
