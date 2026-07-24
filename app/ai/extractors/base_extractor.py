# app/ai/extractors/base_extractor.py

import asyncio
import logging
from pathlib import Path

from google.genai import types

from app.ai.field_catalog import (
    build_default_field_catalog_from_schema,
    format_field_catalog_prompt,
)
from app.ai.gemini_generate import generate_gemini_content
from app.ai.json_utils import parse_gemini_json
from app.ai.smart_field_placement import remap_extraction_result

logger = logging.getLogger(__name__)

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
- Read the ENTIRE document carefully: all pages, tables, headers, footers, stamps, barcodes captions, fine print, and both sides of cards when present in the image/PDF.
- Extract EVERY clearly visible fillable value that maps to the schema — do not stop after the first few fields.
- Only include values clearly supported by the uploaded document.
- Prefer concrete extracted strings for names, dates, addresses, policy numbers, account labels, locations, and notes.
- Place each value in the exact schema field it belongs to. Do not leave a value only in notes when a dedicated field exists.
- Understand wording mismatches against field labels: decide what the value MEANS first, then choose the one exact catalog key. Do not confuse similar labels.
- Read tables row-by-row and multi-column layouts fully.
- Do not extract or return raw passwords.
- Do not extract or return raw PINs.
- Do not extract or return full SSN/social security numbers.
- Do not extract or return full credit/debit card numbers unless the schema asks only for last 4 digits.
- If a document contains passwords, PINs, SSNs, recovery codes, seed phrases, MFA backup codes, or full card numbers, return a safe note like "Stored in uploaded document" only if the schema has a note/location field.
- Omit null/empty fields from patch when possible; keep useful extracted values.
- Never include prompt text, internal reasoning, or hidden metadata.
"""


def _prune_empty(value):
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else None
    if isinstance(value, list):
        items = [_prune_empty(item) for item in value]
        items = [item for item in items if item not in (None, {}, [])]
        return items
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            pruned = _prune_empty(item)
            if pruned in (None, {}, []):
                continue
            cleaned[key] = pruned
        return cleaned
    return value


def normalize_extraction_result(parsed: dict | None) -> dict:
    if not isinstance(parsed, dict):
        return {
            "section": None,
            "scope": "section",
            "subsection": None,
            "confidence": 0,
            "patch": {},
        }

    result = dict(parsed)
    patch = result.get("patch")
    meta_keys = {"section", "scope", "subsection", "confidence", "patch"}

    if not isinstance(patch, dict):
        promoted = {
            key: value
            for key, value in result.items()
            if key not in meta_keys
        }
        patch = promoted if promoted else {}

    result["patch"] = _prune_empty(patch) or {}
    if "confidence" not in result:
        result["confidence"] = 0.5 if result["patch"] else 0
    return result


def _extract_sync(
    *,
    document_url: str,
    mime_type: str,
    prompt: str,
    response_schema: dict,
    field_catalog: list[dict] | None = None,
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

    final_prompt = f"""
{prompt}

{GLOBAL_PRIVACY_EXTRACTION_RULES}
"""

    response = generate_gemini_content(
        contents=[
            types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
            final_prompt,
        ],
        response_mime_type="application/json",
        response_json_schema=response_schema,
        temperature=0,
        max_output_tokens=16384,
    )

    raw_text = getattr(response, "text", None) or ""
    if not raw_text and getattr(response, "candidates", None):
        try:
            parts = response.candidates[0].content.parts or []
            raw_text = "".join(getattr(part, "text", "") or "" for part in parts)
        except Exception:
            raw_text = ""

    try:
        parsed = parse_gemini_json(raw_text)
    except RuntimeError:
        raise RuntimeError("Gemini returned invalid JSON")

    normalized = normalize_extraction_result(parsed)
    # Understand meaning → place onto exact catalog field keys/labels.
    return remap_extraction_result(normalized, field_catalog)


async def extract_structured_from_document(
    *,
    document_url: str,
    mime_type: str,
    prompt: str,
    response_schema: dict,
    field_catalog: list[dict] | None = None,
):
    catalog = field_catalog or build_default_field_catalog_from_schema(
        response_schema
    )
    catalog_prompt = format_field_catalog_prompt(catalog)

    return await asyncio.to_thread(
        _extract_sync,
        document_url=document_url,
        mime_type=mime_type,
        prompt=f"{prompt}{catalog_prompt}",
        response_schema=response_schema,
        field_catalog=catalog,
    )
