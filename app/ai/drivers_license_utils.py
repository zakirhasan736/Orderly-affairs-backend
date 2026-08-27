"""Harvest driver's license / state ID fields from OCR / document text."""

from __future__ import annotations

import re

from app.ai.semantic_field_map import as_plain_text, normalize_date_to_iso

_DL_NUMBER_RE = re.compile(
    r"\b(?:"
    r"D\.?L\.?\s*(?:#|no\.?|number|num)?"
    r"|driver'?s?\s*licen[cs]e\s*(?:#|no\.?|number|num)?"
    r"|licen[cs]e\s*(?:#|no\.?|number|num)?"
    r"|state\s*id\s*(?:#|no\.?|number)?"
    r"|id\s*(?:#|no\.?|number)"
    r")\s*[:#=\-]?\s*"
    r"(?P<num>[A-Z0-9][A-Z0-9\-]{4,20})\b",
    re.IGNORECASE,
)

_DD_NUMBER_RE = re.compile(
    r"\b(?:"
    r"DD\s*(?:#|no\.?|number|num)?"
    r"|document\s*discriminator"
    r"|audit\s*(?:#|no\.?|number|num)?"
    r")\s*[:#=\-]?\s*"
    r"(?P<dd>[A-Z0-9][A-Z0-9\-]{0,12})\b",
    re.IGNORECASE,
)

_CLASS_RE = re.compile(
    r"\b(?:class|cls)\s*[:#=\-]?\s*(?P<cls>[A-Z0-9]{1,3})\b",
    re.IGNORECASE,
)

_ISSUE_RE = re.compile(
    r"\b(?:iss(?:ued)?|issue\s*date|date\s*issued)\s*[:#=\-]?\s*"
    r"(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)

_EXPIRY_RE = re.compile(
    r"\b(?:exp(?:ires|iration)?|exp\s*date|valid\s*(?:thru|through|until))\s*[:#=\-]?\s*"
    r"(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)

_DOB_RE = re.compile(
    r"\b(?:DOB|d\.?o\.?b\.?|date\s*of\s*birth|birth\s*date|birthdate)\s*[:#=\-]?\s*"
    r"(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)


def harvest_drivers_license_fields(text: object) -> dict[str, str]:
    """Return any DL fields found in free text."""
    blob = str(text or "")
    if not blob.strip():
        return {}

    found: dict[str, str] = {}

    dl = _DL_NUMBER_RE.search(blob)
    if dl:
        found["drivers_license_number"] = dl.group("num").strip().upper()

    dd = _DD_NUMBER_RE.search(blob)
    if dd:
        dd_value = dd.group("dd").strip().upper()
        if 1 <= len(dd_value) <= 14:
            found["drivers_license_dd_number"] = dd_value

    cls = _CLASS_RE.search(blob)
    if cls:
        found["drivers_license_class"] = cls.group("cls").strip().upper()

    iss = _ISSUE_RE.search(blob)
    if iss:
        iso = normalize_date_to_iso(iss.group("date"))
        if iso:
            found["drivers_license_issue_date"] = iso

    exp = _EXPIRY_RE.search(blob)
    if exp:
        iso = normalize_date_to_iso(exp.group("date"))
        if iso:
            found["drivers_license_expiration_date"] = iso

    dob = _DOB_RE.search(blob)
    if dob:
        iso = normalize_date_to_iso(dob.group("date"))
        if iso:
            found["date_of_birth"] = iso

    return found


def recover_drivers_license_for_vital_result(
    result: dict | None,
    document_text: str | None = None,
) -> dict | None:
    """Fill empty vital_info DL fields from item text and/or document OCR.

    Labeled DOB in the document overrides a prior vital DOB — OCR/AI often
    confuses issue/expiry with birth, and the DatePicker used to shift ISO
    dates by one day in US timezones.
    """
    if not isinstance(result, dict):
        return result

    patch = result.get("patch") if isinstance(result.get("patch"), dict) else None
    if not isinstance(patch, dict):
        patch = {}

    vital = patch.get("vital_info")
    if isinstance(vital, list) and vital and isinstance(vital[0], dict):
        vital = vital[0]
    if not isinstance(vital, dict):
        vital = {}

    next_vital = dict(vital)
    sources = [
        "\n".join(
            as_plain_text(vital.get(key)) or ""
            for key in (
                "full_legal_name",
                "other_names",
                "social_security_number",
                "drivers_license_number",
                "drivers_license_dd_number",
                "drivers_license_class",
                "security_question_answers",
                "frequent_pins",
            )
        ),
        str(document_text or ""),
    ]

    for source in sources:
        harvested = harvest_drivers_license_fields(source)
        for key, value in harvested.items():
            if not value:
                continue
            # Prefer an explicitly labeled DOB from the document over a prior guess.
            if key == "date_of_birth":
                next_vital[key] = value
                continue
            # A glued-together barcode is not a DD / audit number.
            if key == "drivers_license_dd_number":
                existing = as_plain_text(next_vital.get(key))
                if existing and len(existing) > 14 and 2 <= len(value) <= 14:
                    next_vital[key] = value
                    continue
            if not as_plain_text(next_vital.get(key)):
                next_vital[key] = value

    # Always store DOB as YYYY-MM-DD when we can normalize it.
    existing_dob = as_plain_text(next_vital.get("date_of_birth"))
    if existing_dob:
        normalized = normalize_date_to_iso(existing_dob)
        if normalized:
            next_vital["date_of_birth"] = normalized

    if next_vital == vital and patch.get("vital_info") is vital:
        return result

    next_result = dict(result)
    next_patch = dict(patch)
    next_patch["vital_info"] = next_vital
    next_result["patch"] = next_patch
    if "section" not in next_result:
        next_result["section"] = "vital_information"
    return next_result
