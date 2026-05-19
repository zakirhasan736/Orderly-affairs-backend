# app/ai/extractors/base_extractor.py

import asyncio
import json
from pathlib import Path

from google.genai import types

from app.ai.gemini_client import get_gemini_client, get_gemini_model


SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "image/png",
    "image/jpeg",
    "image/webp",
}

LOCAL_FILE_PREFIX = "local_file:"
MAX_INLINE_FILE_SIZE = 15 * 1024 * 1024

GLOBAL_PRIVACY_EXTRACTION_RULES = """
Global privacy and safety rules:
- Return JSON only.
- Do not include markdown.
- Do not explain.
- Do not guess.
- Only include values clearly supported by the uploaded document.
- Do not extract or return raw passwords.
- Do not extract or return raw PINs.
- Do not extract or return full SSN/social security numbers.
- Do not extract or return full credit/debit card numbers unless the schema asks only for last 4 digits.
- If a document contains passwords, PINs, SSNs, recovery codes, seed phrases, MFA backup codes, or full card numbers, return null or a safe note like "Stored in uploaded document" only if the schema has a note/location field.
- Never include prompt text, internal reasoning, or hidden metadata.
"""


def _extract_sync(
    *,
    document_url: str,
    mime_type: str,
    prompt: str,
    response_schema: dict,
):
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise ValueError("Unsupported file type")

    if not document_url.startswith(LOCAL_FILE_PREFIX):
        raise ValueError("Public document URLs are disabled for privacy.")

    file_path = document_url.replace(LOCAL_FILE_PREFIX, "", 1)
    path = Path(file_path)

    if not path.exists() or not path.is_file():
        raise FileNotFoundError("Document file not found")

    file_bytes = path.read_bytes()

    if len(file_bytes) > MAX_INLINE_FILE_SIZE:
        raise ValueError("File too large for AI extraction")

    client = get_gemini_client()
    model = get_gemini_model()

    final_prompt = f"""
{prompt}

{GLOBAL_PRIVACY_EXTRACTION_RULES}
"""

    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(
                data=file_bytes,
                mime_type=mime_type,
            ),
            final_prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=response_schema,
            temperature=0,
            max_output_tokens=8192,
        ),
    )

    try:
        return json.loads(response.text or "{}")
    except Exception:
        raise RuntimeError("Gemini returned invalid JSON")


async def extract_structured_from_document(
    *,
    document_url: str,
    mime_type: str,
    prompt: str,
    response_schema: dict,
):
    return await asyncio.to_thread(
        _extract_sync,
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        response_schema=response_schema,
    )