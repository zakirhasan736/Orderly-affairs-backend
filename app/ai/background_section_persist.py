"""Server-side section persist after AI extract — survives browser/device disconnect."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.repositories.section_repository import SectionRepository
from app.security.section_crypto import decrypt_section_data, encrypt_section_data

logger = logging.getLogger(__name__)

# AI section key → vault section metadata
SECTION_PERSIST_META: dict[str, dict[str, Any]] = {
    "vehicles": {
        "section_id": "5",
        "section_key": "section5_vehicles",
        "array_keys": ["5A"],
        "subsections": ["5A"],
    },
    "insurance_policies": {
        "section_id": "7",
        "section_key": "section7_insurance_policies",
        "array_keys": ["7A"],
        "subsections": ["7A"],
    },
    "community_memberships": {
        "section_id": "8",
        "section_key": "section8_community_membership",
        "array_keys": ["8A"],
        "subsections": ["8A"],
    },
    "main_residence": {
        "section_id": "6",
        "section_key": "section6_main_residence",
        "array_keys": ["6A"],
        "subsections": ["6A"],
    },
    "banking_financial_accounts": {
        "section_id": "12",
        "section_key": "section12_banking_financial_accounts",
        "array_keys": ["12A", "12B"],
        "subsections": ["12A", "12B"],
    },
    "passwords_online_accounts": {
        "section_id": "13",
        "section_key": "section13_passwords_online_accounts",
        "array_keys": ["13A"],
        "subsections": ["13A"],
    },
    "charitable_giving": {
        "section_id": "9",
        "section_key": "section9_charitable_giving",
        "array_keys": ["9A"],
        "subsections": ["9A"],
    },
    "investment_accounts": {
        "section_id": "14",
        "section_key": "section14_investment_accounts",
        "array_keys": ["14A"],
        "subsections": ["14A"],
    },
    "credit_cards_debt": {
        "section_id": "16",
        "section_key": "section16_credit_cards_debt",
        "array_keys": ["16A", "16B"],
        "subsections": ["16A", "16B"],
    },
    "health_information": {
        "section_id": "15",
        "section_key": "section15_health_information",
        "array_keys": ["15A", "15B"],
        "subsections": ["15A", "15B"],
    },
    "assets_valuables": {
        "section_id": "19",
        "section_key": "section19_assets_valuables",
        "array_keys": ["19A", "19B"],
        "subsections": ["19A", "19B"],
    },
    "legal_documents_records": {
        "section_id": "20",
        "section_key": "section20_legal_documents_records",
        "array_keys": ["20A", "20B", "20C"],
        "subsections": ["20A", "20B", "20C"],
    },
}


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("label", "name", "value", "text", "title", "type"):
            nested = _as_text(value.get(key))
            if nested:
                return nested
    return ""


def _normalize_comparable(value: Any) -> str:
    text = _as_text(value).lower()
    return re.sub(r"\s+", " ", text).strip()


def _normalize_policy_number(value: Any) -> str:
    text = _normalize_comparable(value)
    return re.sub(r"[\s\-_.#]", "", text)


def _insurance_company(item: dict) -> str:
    return _normalize_comparable(
        item.get("policy_company")
        or item.get("insurance_company")
        or item.get("provider")
        or item.get("carrier")
        or item.get("company")
    )


def _policy_type(item: dict) -> str:
    return _normalize_comparable(item.get("policy_type") or item.get("type"))


def _companies_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    return a in b or b in a


def _insurance_policies_are_duplicates(existing: dict, incoming: dict) -> bool:
    """Match frontend insurancePoliciesAreDuplicates — renew same policy, else add."""
    existing_policy = _normalize_policy_number(existing.get("policy_number"))
    incoming_policy = _normalize_policy_number(incoming.get("policy_number"))

    if existing_policy and incoming_policy:
        return existing_policy == incoming_policy

    # Different / incomplete numbers → treat as a separate policy card
    if existing_policy or incoming_policy:
        return False

    existing_company = _insurance_company(existing)
    incoming_company = _insurance_company(incoming)
    existing_type = _policy_type(existing)
    incoming_type = _policy_type(incoming)
    existing_other = _normalize_comparable(
        existing.get("policy_type_other") or existing.get("type_other")
    )
    incoming_other = _normalize_comparable(
        incoming.get("policy_type_other") or incoming.get("type_other")
    )

    if not (
        existing_company
        and incoming_company
        and existing_type
        and incoming_type
        and _companies_match(existing_company, incoming_company)
        and existing_type == incoming_type
    ):
        return False

    if existing_type == "other" and existing_other and incoming_other:
        if existing_other != incoming_other:
            return False

    return True


def _vehicles_are_duplicates(existing: dict, incoming: dict) -> bool:
    existing_vin = _normalize_comparable(existing.get("vin"))
    incoming_vin = _normalize_comparable(incoming.get("vin"))
    if existing_vin and incoming_vin and existing_vin == incoming_vin:
        return True
    if existing_vin and incoming_vin and existing_vin != incoming_vin:
        return False

    existing_plate = _normalize_comparable(existing.get("license_plate"))
    incoming_plate = _normalize_comparable(incoming.get("license_plate"))
    if existing_plate and incoming_plate and existing_plate == incoming_plate:
        return True
    if existing_plate and incoming_plate and existing_plate != incoming_plate:
        return False

    year_a = _normalize_comparable(existing.get("year"))
    year_b = _normalize_comparable(incoming.get("year"))
    make_a = _normalize_comparable(existing.get("make"))
    make_b = _normalize_comparable(incoming.get("make"))
    model_a = _normalize_comparable(existing.get("model"))
    model_b = _normalize_comparable(incoming.get("model"))
    if (
        year_a
        and year_b
        and make_a
        and make_b
        and model_a
        and model_b
        and year_a == year_b
        and make_a == make_b
        and model_a == model_b
    ):
        return True

    # Soft match: thin insurance seed ↔ richer vehicle extract share a policy.
    def _policy(item: dict) -> str:
        raw = item.get("insurance_policy") or item.get("policy_number") or ""
        return _normalize_comparable(raw).replace("-", "").replace("_", "").replace(".", "").replace("#", "")

    policy_a = _policy(existing)
    policy_b = _policy(incoming)
    if policy_a and policy_b and policy_a == policy_b:
        existing_identity = bool(
            existing_vin
            or existing_plate
            or (year_a and make_a and model_a)
        )
        incoming_identity = bool(
            incoming_vin
            or incoming_plate
            or (year_b and make_b and model_b)
        )
        if not existing_identity or not incoming_identity:
            return True
        if make_a and make_b and make_a == make_b and (
            not year_a or not year_b or year_a == year_b
        ):
            return True

    return False


def _is_upload_shape(value: Any) -> bool:
    return isinstance(value, dict) and ("text" in value or "files" in value)


def _merge_item(existing: dict, incoming: dict) -> dict:
    """Merge renewal fields into an existing card (non-empty incoming wins)."""
    merged = dict(existing)
    for key, value in incoming.items():
        if key.startswith("__"):
            continue
        if value is None or value == "" or value == []:
            continue

        current = merged.get(key)

        if _is_upload_shape(value) or _is_upload_shape(current):
            incoming_upload = value if _is_upload_shape(value) else {"text": _as_text(value), "files": []}
            existing_upload = (
                current if _is_upload_shape(current) else {"text": "", "files": []}
            )
            incoming_text = _as_text(incoming_upload.get("text"))
            existing_text = _as_text(existing_upload.get("text"))
            incoming_files = incoming_upload.get("files") if isinstance(incoming_upload.get("files"), list) else []
            existing_files = existing_upload.get("files") if isinstance(existing_upload.get("files"), list) else []
            merged[key] = {
                "text": incoming_text or existing_text,
                "files": incoming_files if incoming_files else existing_files,
            }
            continue

        text = _as_text(value)
        if text:
            merged[key] = text if isinstance(value, str) else value
        elif not _as_text(current):
            merged[key] = value
    return merged


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


def _is_duplicate_for_section(array_key: str, existing: dict, incoming: dict) -> bool:
    if array_key == "7A":
        return _insurance_policies_are_duplicates(existing, incoming)
    if array_key == "5A":
        return _vehicles_are_duplicates(existing, incoming)
    return False


def _merge_section_data(
    current: dict,
    array_key: str,
    incoming_items: list[dict],
) -> dict:
    """Upsert policies/vehicles: renew matching cards, append new ones."""
    next_data = dict(current or {})
    existing_items = next_data.get(array_key)
    if not isinstance(existing_items, list):
        existing_items = []

    if not incoming_items:
        return next_data

    if not existing_items:
        next_data[array_key] = [dict(item) for item in incoming_items]
        return next_data

    next_items = [dict(item) for item in existing_items]
    for incoming in incoming_items:
        match_index = next(
            (
                index
                for index, existing in enumerate(next_items)
                if _is_duplicate_for_section(array_key, existing, incoming)
            ),
            None,
        )
        if match_index is not None:
            next_items[match_index] = _merge_item(next_items[match_index], incoming)
        else:
            next_items.append(dict(incoming))

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
    array_keys = meta.get("array_keys") or (
        [meta["array_key"]] if meta.get("array_key") else []
    )
    subsections = meta["subsections"]
    if not array_keys:
        return False

    try:
        existing = await SectionRepository.get(owner_id, section_id)
        current: dict = {}
        if existing and existing.get("encrypted_data"):
            current = decrypt_section_data(
                owner_id, section_id, existing["encrypted_data"]
            ) or {}

        merged = dict(current)
        wrote_any = False
        for array_key in array_keys:
            incoming = _patch_items(result, array_key)
            if not incoming:
                continue
            merged = _merge_section_data(merged, array_key, incoming)
            wrote_any = True

        if not wrote_any:
            return False

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
