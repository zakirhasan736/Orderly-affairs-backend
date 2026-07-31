# app/ai/llm_providers.py
"""Compatibility re-exports — prefer app.ai.llm_generate."""

from app.ai.llm_generate import (  # noqa: F401
    DEFAULT_OPENAI_MODEL,
    PROVIDERS,
    active_brain_info,
    contents_to_text_prompt,
    generate_llm_content,
    resolve_provider_and_model,
)

DEFAULT_MODELS = {
    "openai": DEFAULT_OPENAI_MODEL,
    "own": "orderly-fill-v1",
}


def list_provider_catalog():
    info = active_brain_info()
    return [
        {
            "id": info["provider"],
            "label": "OpenAI gpt-4o-mini"
            if info["provider"] == "openai"
            else "Orderly own model",
            "models": [info["model"]],
            "configured": info["configured"],
            "notes": info["notes"],
        }
    ]


def generate_openai_compatible(**kwargs):
    # Map legacy kwargs
    if "gemini_input" in kwargs and "llm_input" not in kwargs:
        kwargs["llm_input"] = kwargs.pop("gemini_input")
    return generate_llm_content(**kwargs)
