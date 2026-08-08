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
    "member_id": {
        "label": "Health plan member ID",
        "aliases": [
            "member_id",
            "member_number",
            "member_no",
            "member #",
            "subscriber_id",
            "subscriber_number",
        ],
        "targets": {
            "insurance_policies": "member_id",
        },
    },
    "group_number": {
        "label": "Insurance group number",
        "aliases": [
            "group_number",
            "group_no",
            "group_num",
            "group #",
            "grp",
            "grp_number",
            "employer_group",
        ],
        "targets": {
            "insurance_policies": "group_number",
        },
    },
    "plan_name": {
        "label": "Health plan name",
        "aliases": [
            "plan_name",
            "plan",
            "product_name",
            "plan_type",
            "choice_plus",
        ],
        "targets": {
            "insurance_policies": "plan_name",
        },
    },
    "rx_bin": {
        "label": "Pharmacy RxBIN",
        "aliases": ["rx_bin", "rxbin", "bin", "rx bin"],
        "targets": {"insurance_policies": "rx_bin"},
    },
    "rx_pcn": {
        "label": "Pharmacy RxPCN",
        "aliases": ["rx_pcn", "rxpcn", "pcn", "rx pcn"],
        "targets": {"insurance_policies": "rx_pcn"},
    },
    "member_name": {
        "label": "Insured / member name",
        "aliases": [
            "member_name",
            "insured_name",
            "subscriber_name",
            "cardholder_name",
        ],
        "targets": {"insurance_policies": "member_name"},
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
        ],
        "targets": {
            "vehicles": "registration_expiry",
            "insurance_policies": "policy_expiry",
            "7": "policy_expiry",
            "5": "registration_expiry",
        },
    },
    "renewal_date": {
        "label": "Membership / subscription renewal",
        "aliases": [
            "renewal_date",
            "membership_renewal",
            "membership_renewal_date",
            "dues_renewal",
            "dues_date",
            "next_renewal",
            "renews_on",
            "renewal",
            "renews",
        ],
        "targets": {
            "community_memberships": "renewal_date",
            "8": "renewal_date",
            "passwords_online_accounts": "subscription_renewal_date",
            "13": "subscription_renewal_date",
            "banking_financial_accounts": "subscription_renewal_date",
            "12": "subscription_renewal_date",
        },
    },
    "subscription_renewal": {
        "label": "Subscription / plan renewal",
        "aliases": [
            "subscription_renewal_date",
            "subscription_renewal",
            "subscription_expires",
            "plan_renewal",
            "plan_renewal_date",
            "billing_renewal",
            "next_billing_date",
            "next_bill_date",
        ],
        "targets": {
            "passwords_online_accounts": "subscription_renewal_date",
            "13": "subscription_renewal_date",
            "banking_financial_accounts": "subscription_renewal_date",
            "12": "subscription_renewal_date",
            "community_memberships": "renewal_date",
            "8": "renewal_date",
        },
    },
    "account_expiry": {
        "label": "Account / access expiry",
        "aliases": [
            "account_expiry_date",
            "account_expiry",
            "account_expiration",
            "access_expires",
            "trial_ends",
            "trial_end_date",
            "plan_expires",
            "plan_end_date",
        ],
        "targets": {
            "passwords_online_accounts": "account_expiry_date",
            "13": "account_expiry_date",
        },
    },
    "maturity_date": {
        "label": "CD / account maturity",
        "aliases": [
            "cd_maturity_date",
            "maturity_date",
            "maturity",
            "matures",
            "matures_on",
            "cd_maturity",
            "certificate_maturity",
        ],
        "targets": {
            "banking_financial_accounts": "cd_maturity_date",
            "12": "cd_maturity_date",
        },
    },
    "last_statement_date": {
        "label": "Last statement date",
        "aliases": [
            "last_statement_date",
            "statement_date",
            "statement_as_of",
            "as_of_date",
            "statement_period_end",
        ],
        "targets": {
            "banking_financial_accounts": "last_statement_date",
            "12": "last_statement_date",
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
        "aliases": [
            "vin",
            "vehicle_identification_number",
            "vin_number",
            "vehicle_vin",
            "insured_vin",
            "veh_id",
            "vehicle_id",
            "vehicle_id_number",
            "serial_number",
            "identification_number",
        ],
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
    r"expiration|coverage\s*(?:period|ends?)|term|effective|from|"
    r"renew(?:al|s)?(?:\s*(?:date|on|by))?|dues(?:\s*(?:due|date))?|matures?(?:\s*on)?"
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
    r"|"
    # Single date immediately after the keyword (renewal 12/31/2026)
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
            return None

    # Month name: December 31, 2025 / Dec 31 2025
    named = re.match(
        r"^(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$",
        text,
        re.IGNORECASE,
    )
    if named:
        month = _MONTH_NAME_TO_NUM.get(named.group(1).lower().rstrip("."))
        if month:
            try:
                return f"{int(named.group(3)):04d}-{month:02d}-{int(named.group(2)):02d}"
            except Exception:
                return None

    return None


_EMBEDDED_DATE_RE = re.compile(
    r"("
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|"
    r"\d{4}-\d{2}-\d{2}"
    r"|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
    r")",
    re.IGNORECASE,
)


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
            or match.group(6)
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

    # Embedded single date after renew / maturity / expires / statement wording
    if re.search(
        r"\b(renew|renewal|renews|dues|maturity|matures|expir|valid|statement|as of|trial)\b",
        raw,
        re.IGNORECASE,
    ):
        embedded = list(_EMBEDDED_DATE_RE.finditer(raw))
        if embedded:
            return normalize_date_to_iso(embedded[-1].group(1))

    return None


_DATE_CONCEPTS = frozenset(
    {
        "policy_expiry",
        "renewal_date",
        "subscription_renewal",
        "account_expiry",
        "maturity_date",
        "last_statement_date",
    }
)


def infer_date_concept_from_text(key: str, text: str) -> str | None:
    """Classify a free-text date by field name + surrounding wording."""
    blob = f"{_norm(key)} {_norm(text[:160])}"
    if re.search(
        r"\b(subscription renew|plan renew|billing renew|next bill|next billing)\b",
        blob,
    ):
        return "subscription_renewal"
    if re.search(r"\b(renewal|renews|dues|membership renew)\b", blob):
        return "renewal_date"
    if re.search(r"\b(maturity|matures|cd maturity|certificate of deposit)\b", blob):
        return "maturity_date"
    if re.search(r"\b(statement date|as of|statement period|last statement)\b", blob):
        return "last_statement_date"
    if re.search(
        r"\b(account expir|access expir|trial end|plan expir|plan end)\b", blob
    ):
        return "account_expiry"
    if re.search(
        r"\b(policy|coverage|valid through|valid until|expir|period end|term end)\b",
        blob,
    ):
        return "policy_expiry"
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
            # Date concepts: prefer end-of-range / ISO when the value is a period.
            if concept in _DATE_CONCEPTS:
                end = extract_end_date_from_text(text) or normalize_date_to_iso(text)
                found[concept] = end or text
            else:
                found[concept] = text

    # Infer expiry from insurance-ish notes when still missing.
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

    # Infer other date concepts from wording in any field (memberships, CDs, subscriptions).
    for key, value in item.items():
        if key.startswith("__"):
            continue
        text = as_plain_text(value)
        if not text:
            continue
        end = extract_end_date_from_text(text) or normalize_date_to_iso(text)
        if not end:
            continue
        concept = infer_date_concept_from_text(key, text)
        if concept and concept not in found:
            found[concept] = end

    # Last resort for insurance/vehicle docs: any clear period end → policy_expiry.
    if "policy_expiry" not in found:
        for key, value in item.items():
            if key.startswith("__"):
                continue
            end = extract_end_date_from_text(as_plain_text(value))
            if end:
                found["policy_expiry"] = end
                break

    for concept in _DATE_CONCEPTS:
        if concept in found:
            found[concept] = normalize_date_to_iso(found[concept]) or found[concept]

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
            if concept in _DATE_CONCEPTS:
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
