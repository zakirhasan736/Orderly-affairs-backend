"""Shared VIN harvesting for vehicle / auto-insurance AI fills."""

from __future__ import annotations

import re

from app.ai.semantic_field_map import as_plain_text

# ISO 3779 VIN alphabet (excludes I, O, Q).
_VIN_CHAR = r"[A-HJ-NPR-Z0-9]"

# Labeled VIN / vehicle ID / serial patterns common on insurance declarations.
_LABELED_VIN_RE = re.compile(
    rf"\b(?:"
    rf"V\.?I\.?N\.?(?:\s*(?:no\.?|number|#))?"
    rf"|VIN(?:\s*(?:no\.?|number|#))?"
    rf"|veh(?:icle)?\.?\s*id(?:ent(?:ification)?)?(?:\s*(?:no\.?|number|#))?"
    rf"|vehicle\s*(?:identification|serial)(?:\s*(?:no\.?|number|#))?"
    rf"|serial(?:\s*(?:no\.?|number|#))"
    rf"|veh\s*#"
    rf")\s*[:#=\-]?\s*"
    rf"(?P<vin>(?:{_VIN_CHAR}[\s\-]*){{10,16}}{_VIN_CHAR})\b",
    re.IGNORECASE,
)

# Bare 17-character VIN tokens (only when they look like real VINs).
_STANDALONE_VIN_RE = re.compile(rf"\b({_VIN_CHAR}{{17}})\b", re.IGNORECASE)


def normalize_vin(value: object) -> str:
    raw = re.sub(r"[\s\-]", "", str(value or "").upper())
    if not raw:
        return ""
    # Drop characters outside the VIN alphabet.
    cleaned = "".join(ch for ch in raw if ch in "ABCDEFGHJKLMNPRSTUVWXYZ0123456789")
    if 11 <= len(cleaned) <= 17:
        return cleaned
    return ""


def _looks_like_vin(candidate: str) -> bool:
    vin = normalize_vin(candidate)
    if not vin:
        return False
    # Prefer full 17-char VINs; allow shorter only when labeled.
    if len(vin) == 17:
        # Must include both letters and digits for bare tokens.
        has_letter = any(ch.isalpha() for ch in vin)
        has_digit = any(ch.isdigit() for ch in vin)
        return has_letter and has_digit
    return 11 <= len(vin) <= 16


def find_vins_in_text(text: object, *, allow_standalone: bool = True) -> list[str]:
    """Return unique VINs found in text, labeled first then standalone."""
    blob = str(text or "")
    if not blob.strip():
        return []

    found: list[str] = []
    seen: set[str] = set()

    def _push(raw: str, *, labeled: bool) -> None:
        vin = normalize_vin(raw)
        if not vin or vin in seen:
            return
        if labeled:
            if not (11 <= len(vin) <= 17):
                return
        elif not _looks_like_vin(vin):
            return
        # Reject obvious non-VINs like pure years padded into 17 chars.
        if vin.isdigit():
            return
        seen.add(vin)
        found.append(vin)

    for match in _LABELED_VIN_RE.finditer(blob):
        _push(match.group("vin"), labeled=True)

    if allow_standalone:
        for match in _STANDALONE_VIN_RE.finditer(blob):
            _push(match.group(1), labeled=False)

    return found


def first_vin_in_text(text: object) -> str | None:
    vins = find_vins_in_text(text)
    return vins[0] if vins else None


def assign_vins_to_vehicles(
    vehicles: list[dict],
    vins: list[str],
) -> list[dict]:
    """Fill empty vehicle.vin fields from harvested VINs in order.

    Leftover VINs become new thin identity rows — including when the
    incoming vehicles list is empty (empty LLM extract + document VINs).
    """
    if not vins:
        return vehicles

    remaining = [
        vin
        for vin in vins
        if vin
        and vin
        not in {
            (as_plain_text(item.get("vin")) or "").upper()
            for item in vehicles
            if isinstance(item, dict)
        }
    ]
    if not remaining:
        return vehicles

    next_vehicles: list[dict] = []
    vin_index = 0
    for item in vehicles:
        if not isinstance(item, dict):
            next_vehicles.append(item)
            continue
        next_item = dict(item)
        if not as_plain_text(next_item.get("vin")) and vin_index < len(remaining):
            next_item["vin"] = remaining[vin_index]
            vin_index += 1
        next_vehicles.append(next_item)

    # Leftover VINs with no vehicle row become thin identity rows.
    while vin_index < len(remaining):
        next_vehicles.append({"vin": remaining[vin_index]})
        vin_index += 1

    return next_vehicles


def vehicle_item_text_blob(item: dict) -> str:
    parts = [
        as_plain_text(item.get("notes")),
        as_plain_text(item.get("vin")),
        as_plain_text(item.get("vehicle_vin")),
        as_plain_text(item.get("insured_vin")),
        as_plain_text(item.get("financing")),
        as_plain_text(item.get("maintenance_records")),
        as_plain_text(item.get("parking_location")),
        as_plain_text(item.get("spare_keys")),
    ]
    return "\n".join(part for part in parts if part)


def recover_vins_for_vehicle_result(
    result: dict | None,
    document_text: str | None = None,
) -> dict | None:
    """Fill missing vehicle VINs from item notes and/or full document text.

    When the LLM returns an empty 5A but the document text contains VINs
    (common on auto insurance cards), create thin identity rows so Vehicles
    still gets cards the client can accept.
    """
    if not isinstance(result, dict):
        return result

    patch = result.get("patch") if isinstance(result.get("patch"), dict) else None
    if not isinstance(patch, dict):
        # Allow recovery from an empty/malformed patch shell.
        patch = {}

    items = patch.get("5A")
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        items = []

    # First recover from each item's own notes/aliases.
    recovered_items: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            recovered_items.append(item)
            continue
        next_item = dict(item)
        if not as_plain_text(next_item.get("vin")):
            for alias in ("vehicle_vin", "insured_vin", "vin_number"):
                alias_val = normalize_vin(next_item.get(alias))
                if alias_val:
                    next_item["vin"] = alias_val
                    break
        if not as_plain_text(next_item.get("vin")):
            harvested = first_vin_in_text(vehicle_item_text_blob(next_item))
            if harvested:
                next_item["vin"] = harvested
        recovered_items.append(next_item)

    # Then assign leftover VINs from the full document text across empty rows.
    # Empty 5A + document VINs → create new vehicle cards (insurance PDFs).
    doc_vins = find_vins_in_text(document_text or "")
    if doc_vins:
        recovered_items = assign_vins_to_vehicles(recovered_items, doc_vins)

    if not recovered_items:
        return result

    next_result = dict(result)
    next_patch = dict(patch)
    next_patch["5A"] = recovered_items
    next_result["patch"] = next_patch
    if "section" not in next_result:
        next_result["section"] = "vehicles"
    return next_result
