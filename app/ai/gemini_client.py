# app/ai/gemini_client.py

import os
from functools import lru_cache
from google import genai


@lru_cache(maxsize=1)
def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing")

    return genai.Client(api_key=api_key)


def get_gemini_model():
    return os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")