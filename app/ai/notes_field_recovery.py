"""Recover structured fields from notes / long text after AI extraction.

Gemini often dumps policy numbers, companies, and dates into notes or
description fields. This pass harvests them into dedicated schema keys.
"""

from __future__ import annotations

import re
from typing import Any

from app.ai.semantic_field_map import (
    apply_concepts_to_item,
    as_plain_text,
    collect_concepts_from_item,
    extract_end_date_from_text,
    normalize_date_to_iso,
)

# Long-text / catch-all keys that often hide structured facts
_LONG_TEXT_KEYS = (
    "notes",
    "premium_info",
    "membership_details",
    "account_value",
    "account_purpose",
    "importance",
    "closure_instructions",
    "regular_transactions",
    "automatic_payments",
    "security_info",
    "recovery_info",
    "document_description",
    "item_description",
    "property_description",
    "business_description",
    "job_description",
    "home_systems_notes",
    "important_notes",
    "special_notes",
    "service_documents",
    "account_documents",
    "policy_documents",
)

_POLICY_NUMBER_RE = re.compile(
    r"(?:policy\s*(?:#|no\.?|num(?:ber)?|id)?|member\s*(?:id|no\.?|number)|"
    r"certificate\s*(?:#|no\.?|number)|plan\s*(?:#|no\.?|id|number))"
    r"[:\s#]*([A-Z0-9][A-Z0-9\-_/]{3,})",
    re.IGNORECASE,
)

_COMPANY_RE = re.compile(
    r"(?:insurance\s*(?:company|carrier|provider)|carrier|insurer|underwriter|"
    r"company\s*name|provider)"
    r"[:\s]+([A-Za-z0-9][A-Za-z0-9 &.,'\-]{1,80})",
    re.IGNORECASE,
)

# section_ai_key → (array keys to enrich)
_SECTION_ARRAYS: dict[str, tuple[str, ...]] = {
    "vehicles": ("5A",),
    "insurance_policies": ("7A",),
    "community_memberships": ("8A",),
    "banking_financial_accounts": ("12A", "12B"),
    "passwords_online_accounts": ("13A",),
    "main_residence": ("6A",),
    "charitable_giving": ("9A",),
    "credit_cards_debt": ("16A", "16B"),
    "investment_accounts": ("14A",),
    "health_information": ("15A", "15B"),
    "assets_valuables": ("19A", "19B"),
    "legal_documents_records": ("20A", "20B", "20C"),
}


def _harvest_policy_number(blob: str) -> str | None:
    match = _POLICY_NUMBER_RE.search(blob or "")
    if not match:
        return None
    value = (match.group(1) or "").strip(" .,:;")
    return value or None


def _harvest_company(blob: str) -> str | None:
    match = _COMPANY_RE.search(blob or "")
    if match:
        value = (match.group(1) or "").strip(" .,:;")
        if len(value) > 60:
            value = value[:60].rsplit(" ", 1)[0]
        return value or None

    with_match = re.search(
        r"\bwith\s+([A-Z][A-Za-z0-9 &.',-]{1,50}?\s+Insurance(?:\s+Company)?)\b",
        blob or "",
        re.IGNORECASE,
    )
    if with_match:
        return with_match.group(1).strip(" .,:;")

    loose = re.search(
        r"\b([A-Z][A-Za-z0-9 &.',-]{1,40}\s+Insurance(?:\s+Co(?:mpany)?)?)\b",
        blob or "",
    )
    if loose:
        return loose.group(1).strip(" .,:;")
    return None


def _blob_from_item(item: dict) -> str:
    parts: list[str] = []
    for key, value in item.items():
        if key.startswith("__"):
            continue
        text = as_plain_text(value)
        if not text:
            continue
        if key in _LONG_TEXT_KEYS or len(text) > 40:
            parts.append(f"{key}: {text}")
        else:
            parts.append(text)
    return "\n".join(parts)


def recover_item_fields(item: dict, section_key: str) -> dict:
    """Fill empty structured fields from notes / long text on one card."""
    if not isinstance(item, dict):
        return item

    next_item = dict(item)
    concepts = collect_concepts_from_item(next_item)
    next_item = apply_concepts_to_item(next_item, concepts, section_key)

    blob = _blob_from_item(next_item)
    if not blob:
        return next_item

    # Insurance / vehicle identity from prose
    if section_key in ("insurance_policies", "vehicles"):
        policy_target = (
            "policy_number" if section_key == "insurance_policies" else "insurance_policy"
        )
        company_target = (
            "policy_company"
            if section_key == "insurance_policies"
            else "insurance_company"
        )
        if not as_plain_text(next_item.get(policy_target)):
            harvested = _harvest_policy_number(blob)
            if harvested:
                next_item[policy_target] = harvested
        if not as_plain_text(next_item.get(company_target)):
            harvested = _harvest_company(blob)
            if harvested:
                next_item[company_target] = harvested

    # Date targets by section when still empty
    date_targets: list[tuple[str, str]] = []
    if section_key == "insurance_policies":
        date_targets.append(("policy_expiry", "policy_expiry"))
    elif section_key == "vehicles":
        date_targets.append(("registration_expiry", "policy_expiry"))
    elif section_key == "community_memberships":
        date_targets.append(("renewal_date", "renewal_date"))
    elif section_key == "banking_financial_accounts":
        date_targets.extend(
            [
                ("cd_maturity_date", "maturity_date"),
                ("last_statement_date", "last_statement_date"),
                ("subscription_renewal_date", "subscription_renewal"),
            ]
        )
    elif section_key == "passwords_online_accounts":
        date_targets.extend(
            [
                ("subscription_renewal_date", "subscription_renewal"),
                ("account_expiry_date", "account_expiry"),
            ]
        )

    for field_key, concept in date_targets:
        if as_plain_text(next_item.get(field_key)):
            continue
        value = concepts.get(concept)
        if not value:
            # Try long-text keys individually for date wording
            for long_key in _LONG_TEXT_KEYS:
                end = extract_end_date_from_text(as_plain_text(next_item.get(long_key)))
                if end:
                    value = end
                    break
        if value:
            next_item[field_key] = normalize_date_to_iso(value) or value

    return next_item


def recover_fields_from_notes(result: dict | None, section_key: str) -> dict | None:
    """Walk patch arrays and recover structured fields from long text."""
    if not isinstance(result, dict):
        return result

    array_keys = _SECTION_ARRAYS.get(section_key)
    if not array_keys:
        # Generic: enrich any list-of-dicts under patch
        patch = result.get("patch") if isinstance(result.get("patch"), dict) else None
        if not isinstance(patch, dict):
            return result
        next_result = dict(result)
        next_patch = dict(patch)
        for key, value in patch.items():
            if isinstance(value, list):
                next_patch[key] = [
                    recover_item_fields(item, section_key)
                    if isinstance(item, dict)
                    else item
                    for item in value
                ]
            elif isinstance(value, dict):
                next_patch[key] = recover_item_fields(value, section_key)
        next_result["patch"] = next_patch
        return next_result

    patch = result.get("patch") if isinstance(result.get("patch"), dict) else result
    if not isinstance(patch, dict):
        return result

    next_patch = dict(patch)
    changed = False
    for array_key in array_keys:
        items = patch.get(array_key)
        if isinstance(items, list):
            recovered = [
                recover_item_fields(item, section_key) if isinstance(item, dict) else item
                for item in items
            ]
            next_patch[array_key] = recovered
            changed = True
        elif isinstance(items, dict):
            next_patch[array_key] = recover_item_fields(items, section_key)
            changed = True

    if not changed:
        return result

    next_result = dict(result)
    if "patch" in result:
        next_result["patch"] = next_patch
    else:
        next_result.update(next_patch)
    return next_result
