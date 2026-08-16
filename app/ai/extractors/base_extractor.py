# app/ai/extractors/base_extractor.py

import asyncio
import logging
import os
from pathlib import Path

from app.ai.field_catalog import (
    build_default_field_catalog_from_schema,
    format_field_catalog_prompt,
)
from app.ai.llm_generate import generate_llm_content
from app.ai.json_utils import parse_llm_json
from app.ai.llm_context import get_llm_settings
from app.ai.local_document_extract import (
    build_llm_document_contents,
    should_fallback_to_vision,
)
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
- EXACT FIELD MATCH (critical): For every fact in the document, find the ONE catalog/schema input field whose key or label matches that fact, and put the value there. Never leave matched data only in notes when a dedicated field exists.
- If the document text matches a field label (or a close synonym), that field MUST be filled with the corresponding value.
- Do not invent alternate key names. Use only the exact catalog/schema keys.
- Understand wording mismatches against field labels: decide what the value MEANS first, then choose the one exact catalog key. Do not confuse similar labels.
- Misspelled OCR labels still map (Polcy Numbor → policy_number). Never change a VALUE because the label was misspelled.
- Distinct people stay distinct: Policy Holder vs Agent vs Beneficiary.
- Distinct dates stay distinct: Effective Date vs Expiration Date vs Renewal Date. Do not merge them unless the catalog has only one matching date field.
- If the document contains BOTH expiration and renewal dates, map each using field descriptions. If unsure, omit rather than guess.
- Read tables row-by-row and multi-column layouts fully. Labels may sit above values, not only as "Label: Value".
- MULTI-ITEM RULE: If the document describes multiple policies, accounts, vehicles, memberships, or people, return one object per entity in the subsection array — never merge them into one object. Exception: one insurance policy / one military discharge / one continuous enlistment is ONE object (coverage lines, duty stations, and awards stay on that object). Same document re-extract should describe the same entity the same way so clients can update instead of duplicating.
- MULTI-ITEM RULE: If the document describes multiple policies, accounts, vehicles, memberships, or people, return one object per entity in the subsection array — never merge them into one object. Exception: one insurance policy / one military discharge / one continuous enlistment is ONE object (coverage lines, duty stations, and awards stay on that object). Same document re-extract should describe the same entity the same way so clients can update instead of duplicating.
- SECTION MATCH RULE: Only fill fields that belong to the requested section/subsection schema. Do not invent parallel subsection cards for the same entity.
- LONG TEXT RULE: Put short facts (IDs, dates, names, amounts, dropdown values) into dedicated fields. Use notes/description TextAreas only for leftover prose that does not fit another field.
- DATE RULE: Fill expiry / renewal / maturity / statement date fields whenever those dates appear. Prefer ISO YYYY-MM-DD. For periods, use the END date.
- Do not extract or return raw passwords. If a password schema field exists and the document shows a password, set that field to "Stored in uploaded document" (never the raw password).
- Do not extract or return raw PINs. If a PIN schema field exists and the document shows a PIN, set that field to "Stored in uploaded document".
- Do not extract or return a full 9-digit SSN. If social_security_number (or similar) exists in the schema, fill last 4 digits only (e.g. "6781" or "***-**-6781") or "Stored in uploaded document".
- Do not extract or return full credit/debit card numbers unless the schema asks only for last 4 digits.
- If a document contains recovery codes, seed phrases, MFA backup codes, or full card numbers with no matching schema field, omit them (or use a storage note only when a note/location field exists).
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


HIGH_AUTOFILL_CONFIDENCE = 0.95
REVIEW_MIN_CONFIDENCE = 0.80


def _confidence_band(value: float) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    if score > 1.0:
        score = score / 100.0
    if score >= HIGH_AUTOFILL_CONFIDENCE:
        return "high"
    if score >= REVIEW_MIN_CONFIDENCE:
        return "medium"
    return "low"


def _count_extracted_fields(result: dict | None) -> int:
    count = 0
    for item in _iter_patch_items(result):
        for key, value in item.items():
            if str(key).startswith("__"):
                continue
            if value in (None, "", [], {}):
                continue
            if isinstance(value, str) and not value.strip():
                continue
            count += 1
    return count


def merge_without_overwriting_existing(existing: dict | None, incoming: dict | None) -> dict:
    """Keep user-entered values; fill only empty keys from AI (never silent overwrite)."""
    if not isinstance(existing, dict):
        return dict(incoming or {})
    merged = dict(existing)
    if not isinstance(incoming, dict):
        return merged
    for key, value in incoming.items():
        if str(key).startswith("__"):
            continue
        current = merged.get(key)
        empty = (
            current is None
            or current == ""
            or current == []
            or current == {}
            or (isinstance(current, str) and not current.strip())
        )
        if empty and value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _run_llm_extract(
    *,
    path: Path,
    mime_type: str,
    final_prompt: str,
    response_schema: dict,
    field_catalog: list[dict] | None,
    force_vision: bool = False,
    operation: str = "extract",
):
    contents, extract_meta = build_llm_document_contents(
        path=path,
        mime_type=mime_type,
        prompt=final_prompt,
        force_vision=force_vision,
    )
    llm_input = str(
        extract_meta.get("llm_input")
        or extract_meta.get("gemini_input")
        or "text"
    )
    logger.info(
        "LLM extract path=%s method=%s quality=%s score=%.2f terra=%s file=%s",
        llm_input,
        extract_meta.get("method"),
        extract_meta.get("quality") or ("bad" if extract_meta.get("needs_vision") else "good"),
        float(extract_meta.get("quality_score") or 0),
        int(bool(extract_meta.get("terra_invoked"))),
        path.name,
    )

    response = generate_llm_content(
        contents=contents,
        response_mime_type="application/json",
        response_json_schema=response_schema,
        temperature=0,
        max_output_tokens=8192,
        operation=operation,
        llm_input=llm_input,
        file_name=path.name,
        role="sol",
    )

    raw_text = getattr(response, "text", None) or ""
    if not raw_text and getattr(response, "candidates", None):
        try:
            parts = response.candidates[0].content.parts or []
            raw_text = "".join(getattr(part, "text", "") or "" for part in parts)
        except Exception:
            raw_text = ""

    try:
        parsed = parse_llm_json(raw_text)
    except RuntimeError:
        raise RuntimeError("LLM returned invalid JSON")

    normalized = normalize_extraction_result(parsed)
    remapped = remap_extraction_result(normalized, field_catalog)
    usage = getattr(response, "_orderly_usage", None)
    if isinstance(usage, dict):
        extract_meta = {
            **extract_meta,
            "system_prompt": usage.get("system_prompt"),
            "user_prompt": usage.get("user_prompt"),
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens"),
                "candidates_tokens": usage.get("candidates_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "estimated_usd": usage.get("estimated_usd"),
            },
            "teacher_provider": usage.get("provider"),
            "teacher_model": usage.get("model"),
        }
    return remapped, extract_meta, usage if isinstance(usage, dict) else None


_run_gemini_extract = _run_llm_extract


def _extract_sync(
    *,
    document_url: str,
    mime_type: str,
    prompt: str,
    response_schema: dict,
    field_catalog: list[dict] | None = None,
    operation: str = "extract",
):
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise ValueError("Unsupported file type")

    if not document_url.startswith(LOCAL_FILE_PREFIX):
        raise ValueError("Public document URLs are disabled for privacy.")

    file_path = document_url.replace(LOCAL_FILE_PREFIX, "", 1)
    path = Path(file_path)

    if not path.exists() or not path.is_file():
        raise FileNotFoundError("Document file not found")

    file_size = path.stat().st_size
    if file_size > MAX_INLINE_FILE_SIZE:
        raise ValueError("File too large for AI extraction")

    final_prompt = f"""
{prompt}

{get_llm_settings().get("few_shot_prompt") or ""}

{GLOBAL_PRIVACY_EXTRACTION_RULES}
"""

    remapped, extract_meta, usage_a = _run_llm_extract(
        path=path,
        mime_type=mime_type,
        final_prompt=final_prompt,
        response_schema=response_schema,
        field_catalog=field_catalog,
        force_vision=False,
        operation=operation,
    )
    usages: list[dict] = [usage_a] if usage_a else []

    # Vision retry when OCR text path is empty/weak.
    if should_fallback_to_vision(remapped, extract_meta):
        remapped, extract_meta, usage_b = _run_llm_extract(
            path=path,
            mime_type=mime_type,
            final_prompt=final_prompt,
            response_schema=response_schema,
            field_catalog=field_catalog,
            force_vision=True,
            operation=f"{operation}_vision_fallback",
        )
        if usage_b:
            usages.append(usage_b)

    total_usd = sum(float(u.get("estimated_usd") or 0) for u in usages)
    total_tokens = sum(int(u.get("total_tokens") or 0) for u in usages)
    for terra_usage in extract_meta.get("terra_usage") or []:
        if isinstance(terra_usage, dict):
            total_usd += float(terra_usage.get("estimated_usd") or 0)
            total_tokens += int(terra_usage.get("total_tokens") or 0)
    inputs = ",".join(
        str(u.get("llm_input") or u.get("gemini_input") or "?") for u in usages
    ) or str(
        extract_meta.get("llm_input")
        or extract_meta.get("gemini_input")
        or "text"
    )
    logger.info(
        "LLM DOC_TOTAL op=%s file=%s calls=%s llm_input=%s pipeline=%s terra=%s "
        "section=%s fields=%s total_tokens=%s ~usd=%.6f",
        operation,
        path.name,
        len(usages),
        inputs,
        extract_meta.get("pipeline_path") or "ocr_sol",
        int(bool(extract_meta.get("terra_invoked"))),
        (remapped or {}).get("section") if isinstance(remapped, dict) else None,
        _count_extracted_fields(remapped),
        total_tokens,
        total_usd,
    )

    fill_band = _confidence_band(
        float((remapped or {}).get("confidence") or 0)
        if isinstance(remapped, dict)
        else 0.0
    )

    if isinstance(remapped, dict):
        remapped["__extract_meta"] = {
            "method": extract_meta.get("method"),
            "quality_score": extract_meta.get("quality_score"),
            "quality": extract_meta.get("quality"),
            "llm_input": extract_meta.get("llm_input")
            or extract_meta.get("gemini_input")
            or "text",
            "gemini_input": extract_meta.get("llm_input")
            or extract_meta.get("gemini_input")
            or "text",
            "read_source": extract_meta.get("read_source") or "system",
            "needs_vision": bool(extract_meta.get("needs_vision")),
            "terra_invoked": bool(extract_meta.get("terra_invoked")),
            "terra_pages": extract_meta.get("terra_pages") or [],
            "pipeline_path": extract_meta.get("pipeline_path") or "ocr_sol",
            "source_method": extract_meta.get("source_method") or "ocr",
            "result_confidence": remapped.get("confidence"),
            "confidence_band": fill_band,
            "llm_calls": len(usages),
            "estimated_usd": round(total_usd, 6),
            "total_tokens": total_tokens,
            "document_text": extract_meta.get("document_text"),
            "system_prompt": extract_meta.get("system_prompt"),
            "user_prompt": extract_meta.get("user_prompt"),
            "usage": extract_meta.get("usage"),
            "teacher_provider": extract_meta.get("teacher_provider"),
            "teacher_model": extract_meta.get("teacher_model"),
            "field_catalog": field_catalog,
        }

    return remapped


def _iter_patch_items(result: dict | None):
    if not isinstance(result, dict):
        return
    patch = result.get("patch") if isinstance(result.get("patch"), dict) else None
    if not isinstance(patch, dict):
        return
    for value in patch.values():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item
        elif isinstance(value, dict):
            yield value


def _empty_catalog_keys(result: dict | None, field_catalog: list[dict] | None) -> list[str]:
    """Catalog keys that are still empty across all patch items."""
    if not field_catalog:
        return []
    keys = [
        str(item.get("key") or item.get("field_key") or "").strip()
        for item in field_catalog
        if isinstance(item, dict)
    ]
    keys = [key for key in keys if key]
    if not keys:
        return []

    filled: set[str] = set()
    items = list(_iter_patch_items(result))
    if not items:
        return keys

    for item in items:
        for key in keys:
            value = item.get(key)
            if value is None or value == "" or value == [] or value == {}:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            filled.add(key)

    return [key for key in keys if key not in filled]


def _needs_pass_b(result: dict | None, field_catalog: list[dict] | None) -> bool:
    # Cost control: second extract pass is optional (AI_ENABLE_PASS_B=1).
    if (os.getenv("AI_ENABLE_PASS_B") or os.getenv("GEMINI_ENABLE_PASS_B") or "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False

    empty = _empty_catalog_keys(result, field_catalog)
    # Only refill when several fields are still empty and we already got signal.
    if len(empty) < 3:
        return False
    has_signal = False
    for item in _iter_patch_items(result):
        for key, value in item.items():
            if key.startswith("__"):
                continue
            text = value if isinstance(value, str) else ""
            if isinstance(value, dict):
                text = str(value.get("text") or "")
            if text and len(text.strip()) >= 4:
                has_signal = True
                break
        if has_signal:
            break
    return has_signal


def _merge_pass_b_into(primary: dict | None, secondary: dict | None) -> dict | None:
    """Fill only empty fields on primary from secondary (Pass B)."""
    if not isinstance(primary, dict):
        return secondary
    if not isinstance(secondary, dict):
        return primary

    primary_patch = primary.get("patch") if isinstance(primary.get("patch"), dict) else {}
    secondary_patch = (
        secondary.get("patch") if isinstance(secondary.get("patch"), dict) else {}
    )
    if not isinstance(primary_patch, dict) or not isinstance(secondary_patch, dict):
        return primary

    next_patch = dict(primary_patch)
    for sub_key, primary_val in primary_patch.items():
        secondary_val = secondary_patch.get(sub_key)
        if isinstance(primary_val, list) and isinstance(secondary_val, list):
            merged_items = []
            for index, item in enumerate(primary_val):
                if not isinstance(item, dict):
                    merged_items.append(item)
                    continue
                other = secondary_val[index] if index < len(secondary_val) else None
                if not isinstance(other, dict):
                    merged_items.append(item)
                    continue
                merged = dict(item)
                for key, value in other.items():
                    if key.startswith("__"):
                        continue
                    existing = merged.get(key)
                    empty = (
                        existing is None
                        or existing == ""
                        or existing == []
                        or existing == {}
                        or (isinstance(existing, str) and not existing.strip())
                    )
                    if empty and value not in (None, "", [], {}):
                        merged[key] = value
                merged_items.append(merged)
            # Append extra entities discovered only in Pass B
            if len(secondary_val) > len(primary_val):
                for extra in secondary_val[len(primary_val) :]:
                    if isinstance(extra, dict):
                        merged_items.append(extra)
            next_patch[sub_key] = merged_items
        elif isinstance(primary_val, dict) and isinstance(secondary_val, dict):
            merged = dict(primary_val)
            for key, value in secondary_val.items():
                existing = merged.get(key)
                empty = (
                    existing is None
                    or existing == ""
                    or (isinstance(existing, str) and not existing.strip())
                )
                if empty and value not in (None, "", [], {}):
                    merged[key] = value
            next_patch[sub_key] = merged
        elif secondary_val and not primary_val:
            next_patch[sub_key] = secondary_val

    next_result = dict(primary)
    next_result["patch"] = next_patch
    return next_result


def _extract_pass_b_sync(
    *,
    document_url: str,
    mime_type: str,
    prompt: str,
    response_schema: dict,
    field_catalog: list[dict] | None,
    empty_keys: list[str],
):
    """Second pass: fill only the still-empty catalog fields."""
    empty_list = ", ".join(empty_keys[:40])
    pass_b_prompt = f"""
{prompt}

PASS B — FILL EMPTY FIELDS ONLY:
The first extraction left these catalog fields empty or incomplete: {empty_list}
Re-read the document and fill ONLY those empty fields (and any other clearly missing schema fields).
Do not clear or overwrite fields that already have good values.
If multiple entities exist, keep one object per entity.
Prefer dedicated fields over dumping into notes.
"""
    return _extract_sync(
        document_url=document_url,
        mime_type=mime_type,
        prompt=pass_b_prompt,
        response_schema=response_schema,
        field_catalog=field_catalog,
        operation="extract_pass_b",
    )


async def extract_structured_from_document(
    *,
    document_url: str,
    mime_type: str,
    prompt: str,
    response_schema: dict,
    field_catalog: list[dict] | None = None,
):
    from app.ai.notes_field_recovery import recover_fields_from_notes

    catalog = field_catalog or build_default_field_catalog_from_schema(
        response_schema
    )
    catalog_prompt = format_field_catalog_prompt(catalog)
    full_prompt = f"{prompt}{catalog_prompt}"

    first = await asyncio.to_thread(
        _extract_sync,
        document_url=document_url,
        mime_type=mime_type,
        prompt=full_prompt,
        response_schema=response_schema,
        field_catalog=catalog,
    )

    section_key = (first or {}).get("section") if isinstance(first, dict) else None
    recovered = recover_fields_from_notes(first, section_key or "") or first

    if _needs_pass_b(recovered, catalog):
        empty_keys = _empty_catalog_keys(recovered, catalog)
        try:
            second = await asyncio.to_thread(
                _extract_pass_b_sync,
                document_url=document_url,
                mime_type=mime_type,
                prompt=full_prompt,
                response_schema=response_schema,
                field_catalog=catalog,
                empty_keys=empty_keys,
            )
            second = recover_fields_from_notes(second, section_key or "") or second
            recovered = _merge_pass_b_into(recovered, second) or recovered
        except Exception as error:
            logger.warning("AI Pass B refill skipped: %s", repr(error))

    return recovered
