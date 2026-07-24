"""Server-side section persist after AI extract — survives browser/device disconnect."""

from __future__ import annotations

import logging
from typing import Any

from app.repositories.section_repository import SectionRepository
from app.security.section_crypto import decrypt_section_data, encrypt_section_data

logger = logging.getLogger(__name__)

# AI section key → vault section metadata
SECTION_PERSIST_META: dict[str, dict[str, Any]] = {
    "vehicles": {
        "section_id": "5",
        "section_key": "section5_vehicles",
        "array_key": "5A",
        "subsections": ["5A"],
    },
    "insurance_policies": {
        "section_id": "7",
        "section_key": "section7_insurance_policies",
        "array_key": "7A",
        "subsections": ["7A"],
    },
}


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    return ""


def _patch_items(result: dict | None, array_key: str) -> list[dict]:
    if not isinstance(result, dict):
        return []
    patch = result.get("patch") if isinstance(result.get("patch"), dict) else result
    if not isinstance(patch, dict):
        return []
    items = patch.get(array_key)
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    if isinstance(items, dict):
        return [items]
    return []


def _merge_item(existing: dict, incoming: dict) -> dict:
    merged = dict(existing)
    for key, value in incoming.items():
        if key.startswith("__"):
            continue
        text = _as_text(value)
        if not text and value not in (0, False):
            # Keep structured values (upload shapes) when present
            if value is None or value == "" or value == []:
                continue
            if not _as_text(merged.get(key)):
                merged[key] = value
            continue
        if not _as_text(merged.get(key)):
            merged[key] = value if not isinstance(value, str) else text
    return merged


def _merge_section_data(
    current: dict,
    array_key: str,
    incoming_items: list[dict],
) -> dict:
    next_data = dict(current or {})
    existing_items = next_data.get(array_key)
    if not isinstance(existing_items, list):
        existing_items = []

    if not incoming_items:
        return next_data

    if not existing_items:
        next_data[array_key] = [dict(item) for item in incoming_items]
        return next_data

    # Merge into first card (typical auto-insurance single policy/vehicle).
    merged_first = _merge_item(existing_items[0], incoming_items[0])
    next_items = [merged_first, *existing_items[1:]]
    # Append extra incoming items beyond the first when clearly new.
    for extra in incoming_items[1:]:
        next_items.append(dict(extra))
    next_data[array_key] = next_items
    return next_data


async def persist_ai_result_to_owner_section(
    *,
    owner_id: str,
    section_ai_key: str,
    result: dict | None,
) -> bool:
    """
    Merge AI extraction into the owner's encrypted section and save.
    Returns True when a write was attempted successfully.
    """
    meta = SECTION_PERSIST_META.get(section_ai_key)
    if not meta or not isinstance(result, dict):
        return False

    section_id = meta["section_id"]
    section_key = meta["section_key"]
    array_key = meta["array_key"]
    subsections = meta["subsections"]
    incoming = _patch_items(result, array_key)
    if not incoming:
        return False

    try:
        existing = await SectionRepository.get(owner_id, section_id)
        current: dict = {}
        if existing and existing.get("encrypted_data"):
            current = decrypt_section_data(
                owner_id, section_id, existing["encrypted_data"]
            ) or {}

        merged = _merge_section_data(current, array_key, incoming)
        encrypted = encrypt_section_data(owner_id, section_id, merged)
        await SectionRepository.upsert(
            owner_id=owner_id,
            section_id=section_id,
            section_key=section_key,
            encrypted_data=encrypted,
            subsections=subsections,
        )
        logger.info(
            "Background AI persist saved section %s for owner %s",
            section_id,
            owner_id,
        )
        return True
    except Exception as error:
        logger.warning(
            "Background AI persist failed for %s/%s: %s",
            owner_id,
            section_ai_key,
            repr(error),
        )
        return False


async def persist_cached_extractions_for_owner(
    *,
    owner_id: str,
    cached_extractions: dict | None,
    section_keys: list[str] | None = None,
) -> None:
    """Persist one or more cached AI section results for the owner."""
    cached = cached_extractions or {}
    keys = section_keys or list(cached.keys())
    for key in keys:
        if key not in SECTION_PERSIST_META:
            continue
        await persist_ai_result_to_owner_section(
            owner_id=owner_id,
            section_ai_key=key,
            result=cached.get(key),
        )
