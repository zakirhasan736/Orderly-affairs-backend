"""
Semantic field concepts: map wording mismatches to meaning, then to section fields.

Example:
  "Policy #", "Member ID", "Certificate No" → policy_number
  → vehicles.insurance_policy AND insurance_policies.policy_number

  "Policy period ends", "Valid through", "Expires" → policy_expiry
  → vehicles.registration_expiry AND insurance_policies.policy_expiry
"""

from __future__ import annotations

import re
from typing import Any


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


# Concept → canonical meaning → per-section target field keys
SEMANTIC_CONCEPTS: dict[str, dict[str, Any]] = {
    "policy_number": {
        "label": "Policy / insurance number",
        "aliases": [
            "policy_number",
            "policy_no",
            "policy_num",
            "policy_id",
            "policy",
            "insurance_policy",
            "insurance_policy_number",
            "insurance_number",
            "insurance_no",
            "insurance_num",
            "insurance_id",
            "ins_policy",
            "ins_policy_number",
            "ins_number",
            "member_id",
            "member_number",
            "member_no",
            "certificate_number",
            "certificate_no",
            "plan_number",
            "plan_id",
            "naic",
            "naic_number",
            "policy #",
            "pol no",
            "pol number",
            "pol_num",
            "pol_number",
        ],
        "targets": {
            "vehicles": "insurance_policy",
            "insurance_policies": "policy_number",
        },
    },
    "policy_company": {
        "label": "Insurance company",
        "aliases": [
            "policy_company",
            "insurance_company",
            "insurance_carrier",
            "insurance_provider",
            "carrier",
            "carrier_name",
            "provider",
            "provider_name",
            "insurer",
            "company",
            "underwriter",
        ],
        "targets": {
            "vehicles": "insurance_company",
            "insurance_policies": "policy_company",
        },
    },
    "policy_expiry": {
        "label": "Policy / registration expiry",
        "aliases": [
            "policy_expiry",
            "policy_expiration",
            "policy_expires",
            "registration_expiry",
            "registration_expiration",
            "expiration_date",
            "expiry_date",
            "expires",
            "expire",
            "valid_through",
            "valid_thru",
            "valid_until",
            "valid_to",
            "policy_period_end",
            "period_end",
            "end_date",
            "coverage_ends",
            "term_end",
            "renewal_date",
        ],
        "targets": {
            "vehicles": "registration_expiry",
            "insurance_policies": "policy_expiry",
        },
    },
    "coverage_amount": {
        "label": "Coverage amount",
        "aliases": [
            "coverage_amount",
            "coverage",
            "coverage_limit",
            "death_benefit",
            "liability_limit",
            "insured_amount",
            "benefit_amount",
        ],
        "targets": {
            "insurance_policies": "coverage_amount",
        },
    },
    "vehicle_vin": {
        "label": "VIN",
        "aliases": ["vin", "vehicle_identification_number", "vin_number"],
        "targets": {"vehicles": "vin"},
    },
    "license_plate": {
        "label": "License plate",
        "aliases": [
            "license_plate",
            "licence_plate",
            "plate",
            "plate_number",
            "tag_number",
            "license_tag",
            "plate_no",
        ],
        "targets": {"vehicles": "license_plate"},
    },
}


def resolve_concept_from_key(key: str) -> str | None:
    n = _norm(key).replace(" ", "_")
    spaced = _norm(key)

    # Exact alias match first (avoid "policy" swallowing "policy_company").
    for concept, meta in SEMANTIC_CONCEPTS.items():
        for alias in meta["aliases"]:
            a = _norm(alias).replace(" ", "_")
            a_spaced = _norm(alias)
            if n == a or spaced == a_spaced:
                return concept

    # Human meaning: "insurance number" / "policy id" ≈ policy number
    if (
        ("insurance" in spaced or "policy" in spaced or "member" in spaced)
        and ("number" in spaced or "num" in spaced or spaced.endswith(" no") or " id" in f" {spaced} ")
        and "plate" not in spaced
        and "company" not in spaced
        and "carrier" not in spaced
        and "type" not in spaced
    ):
        return "policy_number"

    if ("insurance" in spaced or "carrier" in spaced) and (
        "company" in spaced or "provider" in spaced or spaced == "carrier"
    ):
        return "policy_company"

    # Soft contains only for longer aliases (member_id in insurance_member_id).
    for concept, meta in SEMANTIC_CONCEPTS.items():
        for alias in meta["aliases"]:
            a = _norm(alias).replace(" ", "_")
            if len(a) >= 8 and (a in n or n in a):
                if concept == "policy_number" and any(
                    token in n for token in ("company", "carrier", "provider", "type")
                ):
                    continue
                return concept
    return None


def target_field_for_concept(concept: str, section_key: str) -> str | None:
    meta = SEMANTIC_CONCEPTS.get(concept) or {}
    targets = meta.get("targets") or {}
    return targets.get(section_key)


def concept_label(concept: str) -> str:
    meta = SEMANTIC_CONCEPTS.get(concept) or {}
    return str(meta.get("label") or concept.replace("_", " ").title())


def as_plain_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "label", "name", "value", "title", "type"):
            nested = as_plain_text(value.get(key))
            if nested:
                return nested
    return None


_PERIOD_END_RE = re.compile(
    r"(?:"
    r"policy\s*period|period|valid(?:\s*(?:from|thru|through|until))?|expires?(?:\s*on)?|"
    r"expiration|coverage\s*(?:period|ends?)|term|effective|from"
    r")"
    r"[^\d]{0,48}"
    r"(?:"
    # Numeric range: 01/01/2025 to 12/31/2025
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
    r".{0,24}?(?:to|through|thru|until|–|-|—)\s*"
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
    r"|"
    # ISO range: 2025-01-01 to 2025-12-31
    r"(\d{4}-\d{2}-\d{2})"
    r".{0,24}?(?:to|through|thru|until|–|-|—)\s*"
    r"(\d{4}-\d{2}-\d{2})"
    r"|"
    # Single end after to/until/ends
    r"(?:to|through|thru|until|ends?)\s*"
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})"
    r")",
    re.IGNORECASE,
)

# Bare "from DATE to DATE" / "DATE – DATE" without a period keyword.
_BARE_RANGE_RE = re.compile(
    r"(?:"
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})"
    r"\s*(?:to|through|thru|until|–|-|—)\s*"
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})"
    r")",
    re.IGNORECASE,
)

_MONTH_RANGE_RE = re.compile(
    r"(?P<m1>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y1>\d{4})"
    r".{0,24}?(?:to|through|thru|until|–|-|—)\s*"
    r"(?P<m2>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+(?P<d2>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y2>\d{4})",
    re.IGNORECASE,
)

_MONTH_NAME_TO_NUM = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def normalize_date_to_iso(value: str | None) -> str | None:
    """Normalize common date strings to YYYY-MM-DD when possible."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    # Already ISO
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", text)
    if iso:
        return text

    # MM/DD/YYYY or DD/MM/YYYY — prefer US MM/DD when ambiguous (insurance cards).
    slash = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$", text)
    if slash:
        a, b, y = int(slash.group(1)), int(slash.group(2)), int(slash.group(3))
        if y < 100:
            y += 2000 if y < 70 else 1900
        month, day = a, b
        # If first part > 12, treat as D/M/Y
        if a > 12 and b <= 12:
            day, month = a, b
        try:
            return f"{y:04d}-{month:02d}-{day:02d}"
        except Exception:
            return text

    return text


def extract_end_date_from_text(text: str | None) -> str | None:
    """Pull the end date from policy-period / valid-through / from–to wording."""
    if not text:
        return None
    raw = str(text)

    month_match = _MONTH_RANGE_RE.search(raw)
    if month_match:
        m2 = _MONTH_NAME_TO_NUM.get(month_match.group("m2").lower().rstrip("."))
        if m2:
            try:
                return (
                    f"{int(month_match.group('y2')):04d}-"
                    f"{m2:02d}-"
                    f"{int(month_match.group('d2')):02d}"
                )
            except Exception:
                pass

    match = _PERIOD_END_RE.search(raw)
    if match:
        end = (
            match.group(2)
            or match.group(4)
            or match.group(5)
            or match.group(3)
            or match.group(1)
        )
        return normalize_date_to_iso(end)

    bare = _BARE_RANGE_RE.search(raw)
    if bare:
        return normalize_date_to_iso(bare.group(2))

    # Value itself is already a single date
    if re.match(r"^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})$", raw.strip()):
        return normalize_date_to_iso(raw.strip())

    return None


def collect_concepts_from_item(item: dict) -> dict[str, str]:
    """Map concept → plain value from a single vehicle/policy object."""
    found: dict[str, str] = {}

    for key, value in item.items():
        if key.startswith("__"):
            continue
        text = as_plain_text(value)
        if not text:
            continue
        concept = resolve_concept_from_key(key)
        if concept and concept not in found:
            # If expiry field holds a range, keep only the end date.
            if concept == "policy_expiry":
                end = extract_end_date_from_text(text) or normalize_date_to_iso(text)
                found[concept] = end or text
            else:
                found[concept] = text

    # Infer expiry from any text-ish field (period wording often lands in notes/premium).
    if "policy_expiry" not in found:
        preferred_keys = (
            "premium_info",
            "notes",
            "policy_documents",
            "registration_expiry",
            "policy_expiry",
            "policy_period",
            "coverage_period",
            "effective_dates",
            "term",
        )
        for key in preferred_keys:
            end = extract_end_date_from_text(as_plain_text(item.get(key)))
            if end:
                found["policy_expiry"] = end
                break

    if "policy_expiry" not in found:
        for key, value in item.items():
            if key.startswith("__"):
                continue
            end = extract_end_date_from_text(as_plain_text(value))
            if end:
                found["policy_expiry"] = end
                break

    if "policy_expiry" in found:
        found["policy_expiry"] = (
            normalize_date_to_iso(found["policy_expiry"]) or found["policy_expiry"]
        )

    return found


def apply_concepts_to_item(
    item: dict,
    concepts: dict[str, str],
    section_key: str,
) -> dict:
    """Fill empty target fields on an item from semantic concepts."""
    next_item = dict(item)
    for concept, value in concepts.items():
        target = target_field_for_concept(concept, section_key)
        if not target:
            continue
        existing = as_plain_text(next_item.get(target))
        if existing:
            # Still normalize an existing range into an end date.
            if concept == "policy_expiry":
                end = extract_end_date_from_text(existing)
                if end and end != existing:
                    next_item[target] = end
            continue
        next_item[target] = value
    return next_item


def flatten_detected_facts_from_result(
    result: dict | None,
    *,
    section_key: str,
) -> list[dict]:
    """Temporary visualized list of extracted facts for overview / review."""
    if not isinstance(result, dict):
        return []

    patch = result.get("patch") if isinstance(result.get("patch"), dict) else result
    if not isinstance(patch, dict):
        return []

    facts: list[dict] = []
    seen: set[str] = set()

    def add_fact(field_key: str, value: Any, subsection: str | None = None):
        text = as_plain_text(value)
        if not text:
            return
        concept = resolve_concept_from_key(field_key)
        label = concept_label(concept) if concept else field_key.replace("_", " ").title()
        dedupe = f"{concept or field_key}|{text.lower()}"
        if dedupe in seen:
            return
        seen.add(dedupe)
        facts.append(
            {
                "concept": concept,
                "field_key": field_key,
                "label": label,
                "value": text,
                "section_key": section_key,
                "subsection": subsection,
            }
        )

    for sub_key, sub_val in patch.items():
        if isinstance(sub_val, list):
            for item in sub_val:
                if not isinstance(item, dict):
                    continue
                for field_key, value in item.items():
                    add_fact(field_key, value, sub_key)
        elif isinstance(sub_val, dict):
            for field_key, value in sub_val.items():
                add_fact(field_key, value, sub_key)
        else:
            add_fact(sub_key, sub_val)

    return facts
