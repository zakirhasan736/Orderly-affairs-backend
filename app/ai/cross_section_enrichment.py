"""Share overlapping fields between Vehicles (5) and Insurance (7) for the same document."""

from __future__ import annotations

import re

from app.ai.semantic_field_map import (
    apply_concepts_to_item,
    as_plain_text,
    collect_concepts_from_item,
    flatten_detected_facts_from_result,
)

_VIN_RE = re.compile(
    r"\b(?:VIN|vehicle\s*id(?:entification)?\s*(?:no\.?|number|#)?)\s*[:#]?\s*"
    r"([A-HJ-NPR-Z0-9]{11,17})\b",
    re.IGNORECASE,
)
_PLATE_RE = re.compile(
    r"\b(?:license\s*plate|lic(?:ense)?\.?\s*plate|plate(?:\s*#)?|tag)\s*[:#]?\s*"
    r"([A-Z0-9\-]{2,10})\b",
    re.IGNORECASE,
)
_VEHICLE_LABELED_RE = re.compile(
    r"(?:vehicle|auto|car|unit)\s*[:#]\s*"
    r"(?:(?P<year>19\d{2}|20\d{2})\s+)?"
    r"(?P<make>[A-Za-z][A-Za-z0-9\-]{1,20})"
    r"(?:\s+(?P<model>[A-Za-z0-9][A-Za-z0-9 \-/]{0,40}?))?"
    r"(?=\s*(?:;|,|\||$|\n|vin|plate|policy|expir))",
    re.IGNORECASE,
)
_YMM_RE = re.compile(
    r"\b(?P<year>19\d{2}|20\d{2})\s+"
    r"(?P<make>[A-Za-z][A-Za-z0-9\-]{1,20})\s+"
    r"(?P<model>[A-Za-z0-9][A-Za-z0-9 \-/]{1,40}?)"
    r"(?=\s*(?:;|,|\||$|\n|vin|plate|policy|expir))",
    re.IGNORECASE,
)


def _policy_blob(policy: dict) -> str:
    parts = [
        as_plain_text(policy.get("notes")),
        as_plain_text(policy.get("policy_documents")),
        as_plain_text(policy.get("premium_info")),
        as_plain_text(policy.get("coverage_amount")),
        as_plain_text(policy.get("beneficiaries")),
    ]
    return "\n".join(part for part in parts if part)


def _is_auto_insurance_policy(policy: dict) -> bool:
    policy_type = (as_plain_text(policy.get("policy_type")) or "").lower()
    blob = f"{policy_type} {_policy_blob(policy)}".lower()
    is_vehicle = (
        policy_type in {"vehicle", "auto"}
        or "auto" in blob
        or "vin" in blob
        or "license plate" in blob
        or "vehicle" in blob
        or bool(_YMM_RE.search(blob))
    )
    is_home = (
        "homeowner" in blob
        or "renter" in blob
        or "dwelling" in blob
        or "home " in f" {blob} "
    )
    is_life_health = (
        policy_type in {"life", "health", "medical/dental", "disability", "long term care"}
        or ("life insurance" in blob and "auto" not in blob and "vehicle" not in blob)
    )
    return bool(is_vehicle and not is_home and not is_life_health)


def _vehicle_identity_key(vehicle: dict) -> str:
    vin = (as_plain_text(vehicle.get("vin")) or "").upper()
    if vin:
        return f"vin:{vin}"
    plate = (as_plain_text(vehicle.get("license_plate")) or "").upper().replace(" ", "")
    year = as_plain_text(vehicle.get("year")) or ""
    make = (as_plain_text(vehicle.get("make")) or "").lower()
    model = (as_plain_text(vehicle.get("model")) or "").lower()
    if year and make and model:
        return f"ymm:{year}|{make}|{model}"
    if plate:
        return f"plate:{plate}"
    if make and model:
        return f"mm:{make}|{model}"
    return ""


def _parse_vehicles_from_policy_text(blob: str) -> list[dict]:
    """Harvest year/make/model, VIN, and plate lines from insurance notes."""
    if not blob or not str(blob).strip():
        return []

    text = str(blob)
    vehicles: list[dict] = []
    seen: set[str] = set()

    def _push(partial: dict) -> None:
        cleaned = {
            key: value
            for key, value in partial.items()
            if as_plain_text(value)
        }
        if not cleaned:
            return
        key = _vehicle_identity_key(cleaned)
        # Require at least one identity signal so we don't invent blank rows.
        if not key and not (
            cleaned.get("make") or cleaned.get("vin") or cleaned.get("license_plate")
        ):
            return
        dedupe = key or f"idx:{len(vehicles)}|{cleaned.get('make')}|{cleaned.get('model')}"
        if dedupe in seen:
            # Merge richer fields into the earlier row.
            for item in vehicles:
                if _vehicle_identity_key(item) == key or (
                    not key and item.get("make") == cleaned.get("make")
                ):
                    for field, value in cleaned.items():
                        if not as_plain_text(item.get(field)):
                            item[field] = value
                    return
            return
        seen.add(dedupe)
        vehicles.append(cleaned)

    for match in _VEHICLE_LABELED_RE.finditer(text):
        _push(
            {
                "year": (match.group("year") or "").strip() or None,
                "make": (match.group("make") or "").strip() or None,
                "model": (match.group("model") or "").strip() or None,
            }
        )

    for match in _YMM_RE.finditer(text):
        _push(
            {
                "year": (match.group("year") or "").strip() or None,
                "make": (match.group("make") or "").strip() or None,
                "model": (match.group("model") or "").strip() or None,
            }
        )

    vins = [m.group(1).upper() for m in _VIN_RE.finditer(text)]
    plates = [m.group(1).upper() for m in _PLATE_RE.finditer(text)]

    # Attach VINs/plates to parsed vehicles in order; leftover VINs become rows.
    for index, vin in enumerate(vins):
        if index < len(vehicles):
            if not as_plain_text(vehicles[index].get("vin")):
                vehicles[index]["vin"] = vin
        else:
            _push({"vin": vin})

    for index, plate in enumerate(plates):
        if index < len(vehicles):
            if not as_plain_text(vehicles[index].get("license_plate")):
                vehicles[index]["license_plate"] = plate
        else:
            _push({"license_plate": plate})

    return vehicles


def _vehicle_items(result: dict | None) -> list[dict]:
    if not isinstance(result, dict):
        return []
    patch = result.get("patch") if isinstance(result.get("patch"), dict) else result
    items = patch.get("5A") if isinstance(patch, dict) else None
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    if isinstance(items, dict):
        return [items]
    return []


def _insurance_items(result: dict | None) -> list[dict]:
    if not isinstance(result, dict):
        return []
    patch = result.get("patch") if isinstance(result.get("patch"), dict) else result
    items = patch.get("7A") if isinstance(patch, dict) else None
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    if isinstance(items, dict):
        return [items]
    return []


def _enrich_items_in_place(result: dict | None, section_key: str, array_key: str) -> dict | None:
    """Normalize each item so semantic aliases land on the section's canonical fields."""
    if not isinstance(result, dict):
        return result

    patch = result.get("patch")
    if not isinstance(patch, dict):
        return result

    items = patch.get(array_key)
    if not isinstance(items, list):
        return result

    enriched = []
    for item in items:
        if not isinstance(item, dict):
            enriched.append(item)
            continue
        concepts = collect_concepts_from_item(item)
        enriched.append(apply_concepts_to_item(item, concepts, section_key))

    next_result = dict(result)
    next_patch = dict(patch)
    next_patch[array_key] = enriched
    next_result["patch"] = next_patch
    return next_result


def seed_insurance_from_vehicles(vehicle_result: dict | None) -> dict | None:
    """Build insurance patch from vehicle fields using semantic concepts."""
    vehicle_result = _enrich_items_in_place(vehicle_result, "vehicles", "5A")
    policies: list[dict] = []
    seen: set[str] = set()

    for vehicle in _vehicle_items(vehicle_result):
        concepts = collect_concepts_from_item(vehicle)
        company = (
            concepts.get("policy_company")
            or as_plain_text(vehicle.get("insurance_company"))
            or as_plain_text(vehicle.get("policy_company"))
        )
        number = (
            concepts.get("policy_number")
            or as_plain_text(vehicle.get("insurance_policy"))
            or as_plain_text(vehicle.get("policy_number"))
        )
        expiry = (
            concepts.get("policy_expiry")
            or as_plain_text(vehicle.get("registration_expiry"))
            or as_plain_text(vehicle.get("policy_expiry"))
        )

        if company:
            concepts["policy_company"] = company
        if number:
            concepts["policy_number"] = number
        if expiry:
            concepts["policy_expiry"] = expiry

        if not company and not number and not expiry:
            continue

        dedupe = f"{(company or '').lower()}|{(number or '').lower()}"
        if dedupe in seen and dedupe != "|":
            continue
        seen.add(dedupe)

        policy = apply_concepts_to_item(
            {"policy_type": "Vehicle"},
            concepts,
            "insurance_policies",
        )

        notes_bits = []
        year = as_plain_text(vehicle.get("year"))
        make = as_plain_text(vehicle.get("make"))
        model = as_plain_text(vehicle.get("model"))
        plate = as_plain_text(vehicle.get("license_plate"))
        vin = as_plain_text(vehicle.get("vin"))
        label = " ".join(part for part in [year, make, model] if part)
        if label:
            notes_bits.append(f"Vehicle: {label}")
        if plate:
            notes_bits.append(f"Plate: {plate}")
        if vin:
            notes_bits.append(f"VIN: {vin}")
        if notes_bits and not as_plain_text(policy.get("notes")):
            policy["notes"] = "; ".join(notes_bits)

        policies.append(policy)

    if not policies:
        return None

    return {
        "section": "insurance_policies",
        "scope": "section",
        "subsection": None,
        "confidence": 0.75,
        "extraction_source": "cross_seed",
        "patch": {"7A": policies},
    }


def seed_vehicles_from_insurance(insurance_result: dict | None) -> dict | None:
    """Seed vehicle cards from auto insurance — one 5A row per distinct vehicle."""
    insurance_result = _enrich_items_in_place(
        insurance_result, "insurance_policies", "7A"
    )
    policies = _insurance_items(insurance_result)
    if not policies:
        return None

    vehicles: list[dict] = []
    seen: set[str] = set()

    for policy in policies:
        if not _is_auto_insurance_policy(policy):
            continue

        concepts = collect_concepts_from_item(policy)
        shared = apply_concepts_to_item({}, concepts, "vehicles")
        parsed = _parse_vehicles_from_policy_text(_policy_blob(policy))

        # No YMM/VIN/plate in notes → still seed one thin bridge card with policy fields.
        rows = parsed or [{}]
        for row in rows:
            vehicle = dict(shared)
            for key, value in row.items():
                if as_plain_text(value) and not as_plain_text(vehicle.get(key)):
                    vehicle[key] = value
            if not vehicle:
                continue
            key = _vehicle_identity_key(vehicle) or (
                f"policy:{(as_plain_text(vehicle.get('insurance_policy')) or '').lower()}"
                f"|{len(vehicles)}"
            )
            # Collapse thin same-policy duplicates, keep distinct VIN/YMM rows.
            if key in seen:
                continue
            # Soft: another thin row already carries this same policy with no identity.
            identity = _vehicle_identity_key(vehicle)
            policy_no = (
                as_plain_text(vehicle.get("insurance_policy")) or ""
            ).lower()
            if not identity and policy_no:
                already_thin = any(
                    not _vehicle_identity_key(existing)
                    and (
                        as_plain_text(existing.get("insurance_policy")) or ""
                    ).lower()
                    == policy_no
                    for existing in vehicles
                )
                if already_thin:
                    continue
            seen.add(key)
            vehicles.append(vehicle)

    if not vehicles:
        return None

    return {
        "section": "vehicles",
        "scope": "section",
        "subsection": None,
        "confidence": 0.7,
        "extraction_source": "cross_seed",
        "patch": {"5A": vehicles},
    }


def _items_look_same_vehicle(existing: dict, incoming: dict) -> bool:
    """Identity match used when merging seed lists into cached extracts."""
    existing_vin = (as_plain_text(existing.get("vin")) or "").upper()
    incoming_vin = (as_plain_text(incoming.get("vin")) or "").upper()
    if existing_vin and incoming_vin:
        return existing_vin == incoming_vin
    if existing_vin and incoming_vin and existing_vin != incoming_vin:
        return False

    existing_plate = (as_plain_text(existing.get("license_plate")) or "").upper()
    incoming_plate = (as_plain_text(incoming.get("license_plate")) or "").upper()
    if existing_plate and incoming_plate:
        return existing_plate == incoming_plate

    year_a = as_plain_text(existing.get("year")) or ""
    year_b = as_plain_text(incoming.get("year")) or ""
    make_a = (as_plain_text(existing.get("make")) or "").lower()
    make_b = (as_plain_text(incoming.get("make")) or "").lower()
    model_a = (as_plain_text(existing.get("model")) or "").lower()
    model_b = (as_plain_text(incoming.get("model")) or "").lower()
    if year_a and year_b and make_a and make_b and model_a and model_b:
        return year_a == year_b and make_a == make_b and model_a == model_b

    # Thin seed ↔ richer extract on the same policy number only when one side
    # lacks vehicle identity (never collapse two distinct cars on one policy).
    policy_a = re.sub(
        r"[\s\-_.#]",
        "",
        (
            as_plain_text(existing.get("insurance_policy"))
            or as_plain_text(existing.get("policy_number"))
            or ""
        ).lower(),
    )
    policy_b = re.sub(
        r"[\s\-_.#]",
        "",
        (
            as_plain_text(incoming.get("insurance_policy"))
            or as_plain_text(incoming.get("policy_number"))
            or ""
        ).lower(),
    )
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
        # Distinct YMM on same policy = different vehicles.
        if make_a and make_b and make_a != make_b:
            return False
        if model_a and model_b and model_a != model_b:
            return False
        if year_a and year_b and year_a != year_b:
            return False
        if make_a and make_b and make_a == make_b and (
            not year_a or not year_b or year_a == year_b
        ) and (not model_a or not model_b or model_a == model_b):
            return True
    return False


def _items_look_same_policy(existing: dict, incoming: dict) -> bool:
    def _num(item: dict) -> str:
        return re.sub(
            r"[\s\-_.#]",
            "",
            (as_plain_text(item.get("policy_number")) or "").lower(),
        )

    a = _num(existing)
    b = _num(incoming)
    if a and b:
        return a == b
    company_a = (as_plain_text(existing.get("policy_company")) or "").lower()
    company_b = (as_plain_text(incoming.get("policy_company")) or "").lower()
    type_a = (as_plain_text(existing.get("policy_type")) or "").lower()
    type_b = (as_plain_text(incoming.get("policy_type")) or "").lower()
    if company_a and company_b and type_a and type_b:
        companies_match = company_a == company_b or company_a in company_b or company_b in company_a
        if companies_match and type_a == type_b:
            if a or b:
                return True
            return True
    return False


def merge_seed_into_cached(
    existing: dict | None,
    seed: dict | None,
    *,
    array_key: str,
) -> dict | None:
    """Merge seed items into an existing cached extraction without wiping richer data."""
    if not seed:
        return existing
    if not existing:
        return seed

    existing_patch = existing.get("patch") if isinstance(existing.get("patch"), dict) else {}
    seed_patch = seed.get("patch") if isinstance(seed.get("patch"), dict) else {}

    existing_items = existing_patch.get(array_key)
    seed_items = seed_patch.get(array_key)

    if not isinstance(seed_items, list) or not seed_items:
        return existing

    if not isinstance(existing_items, list) or not existing_items:
        merged = dict(existing)
        merged_patch = dict(existing_patch)
        merged_patch[array_key] = seed_items
        merged["patch"] = merged_patch
        return merged

    matcher = (
        _items_look_same_vehicle
        if array_key == "5A"
        else _items_look_same_policy
        if array_key == "7A"
        else None
    )

    merged_items: list = [
        dict(item) if isinstance(item, dict) else item for item in existing_items
    ]
    for donor in seed_items:
        if not isinstance(donor, dict):
            continue
        match_index = -1
        if matcher:
            for index, item in enumerate(merged_items):
                if isinstance(item, dict) and matcher(item, donor):
                    match_index = index
                    break
        elif merged_items and isinstance(merged_items[0], dict):
            match_index = 0

        if match_index >= 0 and isinstance(merged_items[match_index], dict):
            target = dict(merged_items[match_index])
            for key, value in donor.items():
                if target.get(key) in (None, "", [], {}):
                    target[key] = value
            merged_items[match_index] = target
        elif (
            array_key in {"5A", "7A"}
            and len(merged_items) == 1
            and isinstance(merged_items[0], dict)
            and len([x for x in seed_items if isinstance(x, dict)]) == 1
        ):
            # Sole cached card: gap-fill empty fields from the seed unless
            # identity numbers clearly conflict (different policies/vehicles).
            target = dict(merged_items[0])
            if array_key == "7A":
                existing_num = re.sub(
                    r"[\s\-_.#]",
                    "",
                    (as_plain_text(target.get("policy_number")) or "").lower(),
                )
                donor_num = re.sub(
                    r"[\s\-_.#]",
                    "",
                    (as_plain_text(donor.get("policy_number")) or "").lower(),
                )
                if existing_num and donor_num and existing_num != donor_num:
                    merged_items.append(dict(donor))
                else:
                    for key, value in donor.items():
                        if target.get(key) in (None, "", [], {}):
                            target[key] = value
                    merged_items[0] = target
            else:
                # Vehicles: only gap-fill when the sole card lacks identity.
                if not _vehicle_identity_key(target):
                    for key, value in donor.items():
                        if target.get(key) in (None, "", [], {}):
                            target[key] = value
                    merged_items[0] = target
                else:
                    merged_items.append(dict(donor))
        else:
            merged_items.append(dict(donor))

    merged = dict(existing)
    merged_patch = dict(existing_patch)
    merged_patch[array_key] = merged_items
    merged["patch"] = merged_patch
    # Keep a full Gemini extraction marked as such; don't demote to seed.
    if existing.get("extraction_source") in {"llm", "gemini", "openai"}:
        merged["extraction_source"] = existing.get("extraction_source") or "llm"
    elif seed.get("extraction_source") == "cross_seed" and not existing.get(
        "extraction_source"
    ):
        merged["extraction_source"] = "cross_seed"
    return merged


SHARED_VEHICLE_INSURANCE_CONCEPTS = (
    "policy_number",
    "policy_company",
    "policy_expiry",
)


def _first_item_concepts(result: dict | None, array_key: str) -> dict[str, str]:
    if not isinstance(result, dict):
        return {}
    patch = result.get("patch") if isinstance(result.get("patch"), dict) else {}
    items = patch.get(array_key)
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return collect_concepts_from_item(items[0])
    if isinstance(items, dict):
        return collect_concepts_from_item(items)
    return {}


def _shared_policy_concepts(concepts: dict[str, str] | None) -> dict[str, str]:
    if not concepts:
        return {}
    return {
        key: value
        for key, value in concepts.items()
        if key in SHARED_VEHICLE_INSURANCE_CONCEPTS and value
    }


def insurance_result_is_thin(result: dict | None) -> bool:
    """True when insurance patch lacks the core policy identity fields."""
    concepts = _shared_policy_concepts(_first_item_concepts(result, "7A"))
    return not bool(
        concepts.get("policy_number") or concepts.get("policy_company")
    )


def is_cross_seed_extraction(result: dict | None) -> bool:
    """True when the cache entry was copied from a partner section, not Gemini."""
    return isinstance(result, dict) and result.get("extraction_source") == "cross_seed"


def mark_full_extraction(result: dict | None) -> dict | None:
    """Tag a real section extractor result so partner fills can trust it."""
    if not isinstance(result, dict):
        return result
    if result.get("extraction_source") == "cross_seed":
        return result
    next_result = dict(result)
    next_result["extraction_source"] = "llm"
    return next_result


def vehicles_result_is_thin(result: dict | None) -> bool:
    """True when vehicles patch has almost no useful fields."""
    if is_cross_seed_extraction(result):
        return True
    items = _vehicle_items(result)
    if not items or not isinstance(items[0], dict):
        return True
    filled = 0
    for key, value in items[0].items():
        if key in {"id", "_id"}:
            continue
        if as_plain_text(value):
            filled += 1
    return filled < 3


def cached_extraction_needs_full_read(
    section_key: str,
    result: dict | None,
) -> bool:
    """
    Partner / cache reuse must re-read the document when the stored patch is
    only a cross-section seed or otherwise too thin for that section's fields.
    """
    if not isinstance(result, dict):
        return True
    if is_cross_seed_extraction(result):
        return True
    if section_key == "insurance_policies":
        return insurance_cache_missing_policy_number(result) or insurance_result_is_thin(
            result
        )
    if section_key == "vehicles":
        return vehicles_result_is_thin(result)
    return False


def sync_vehicle_insurance_shared_fields(
    cached_extractions: dict | None,
) -> dict:
    """
    After document read + section match: copy shared meanings both ways.

    Policy/insurance number, company, and expiry are the same facts —
    vehicles use insurance_policy / insurance_company / registration_expiry,
    insurance uses policy_number / policy_company / policy_expiry.
    """
    cached = dict(cached_extractions or {})
    vehicle_result = cached.get("vehicles")
    insurance_result = cached.get("insurance_policies")

    vehicle_concepts = _shared_policy_concepts(
        _first_item_concepts(vehicle_result, "5A")
    )
    insurance_concepts = _shared_policy_concepts(
        _first_item_concepts(insurance_result, "7A")
    )

    merged_concepts: dict[str, str] = {}
    for concept in SHARED_VEHICLE_INSURANCE_CONCEPTS:
        merged_concepts[concept] = (
            insurance_concepts.get(concept)
            or vehicle_concepts.get(concept)
            or ""
        )
    merged_concepts = {k: v for k, v in merged_concepts.items() if v}

    # Vehicles have policy data but insurance is blank/thin → build insurance now.
    if vehicle_concepts and insurance_result_is_thin(insurance_result):
        seeded = seed_insurance_from_vehicles(vehicle_result)
        if seeded:
            insurance_result = merge_seed_into_cached(
                insurance_result,
                seeded,
                array_key="7A",
            )
            cached["insurance_policies"] = insurance_result
            insurance_concepts = _shared_policy_concepts(
                _first_item_concepts(insurance_result, "7A")
            )
            for concept in SHARED_VEHICLE_INSURANCE_CONCEPTS:
                if not merged_concepts.get(concept) and insurance_concepts.get(concept):
                    merged_concepts[concept] = insurance_concepts[concept]

    if not merged_concepts:
        return cached

    if vehicle_result:
        vehicle_result = _enrich_items_in_place(vehicle_result, "vehicles", "5A")
        patch = dict(vehicle_result.get("patch") or {})
        items = patch.get("5A")
        if isinstance(items, list) and items:
            items = list(items)
            # Copy shared policy facts onto every vehicle card that still lacks them
            # (multi-car policies share company/number/expiry).
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                items[index] = apply_concepts_to_item(
                    item, merged_concepts, "vehicles"
                )
            patch["5A"] = items
            next_vehicle = dict(vehicle_result)
            next_vehicle["patch"] = patch
            cached["vehicles"] = next_vehicle

    if cached.get("insurance_policies"):
        insurance_result = _enrich_items_in_place(
            cached.get("insurance_policies"), "insurance_policies", "7A"
        )
        patch = dict((insurance_result or {}).get("patch") or {})
        items = patch.get("7A")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            items = list(items)
            items[0] = apply_concepts_to_item(
                items[0], merged_concepts, "insurance_policies"
            )
            # Ensure vehicle auto policies keep type when seeded from vehicles.
            if not as_plain_text(items[0].get("policy_type")) and (
                merged_concepts.get("policy_number")
                or merged_concepts.get("policy_company")
            ):
                items[0]["policy_type"] = "Vehicle"
            # Force-write shared fields even if previous blank strings blocked apply.
            for concept, value in merged_concepts.items():
                if concept == "policy_number" and not as_plain_text(
                    items[0].get("policy_number")
                ):
                    items[0]["policy_number"] = value
                if concept == "policy_company" and not as_plain_text(
                    items[0].get("policy_company")
                ):
                    items[0]["policy_company"] = value
                if concept == "policy_expiry" and not as_plain_text(
                    items[0].get("policy_expiry")
                ):
                    items[0]["policy_expiry"] = value
            patch["7A"] = items
            next_insurance = dict(insurance_result or {})
            next_insurance["patch"] = patch
            cached["insurance_policies"] = next_insurance
    elif merged_concepts.get("policy_number") or merged_concepts.get("policy_company"):
        # Create insurance side from vehicle-only extraction.
        seeded = seed_insurance_from_vehicles(cached.get("vehicles"))
        if seeded:
            cached["insurance_policies"] = seeded

    return cached


def insurance_cache_missing_policy_number(result: dict | None) -> bool:
    """True when cached insurance fill lacks the policy/insurance number."""
    concepts = _first_item_concepts(result, "7A")
    if not concepts:
        return True
    return not bool(concepts.get("policy_number"))


def enrich_primary_result(result: dict | None, section_key: str) -> dict | None:
    """Canonicalize fields on the primary extraction before cache/return."""
    from app.ai.notes_field_recovery import recover_fields_from_notes

    enriched = result
    if section_key == "vehicles":
        enriched = _enrich_items_in_place(result, "vehicles", "5A")
    elif section_key == "insurance_policies":
        enriched = _enrich_items_in_place(result, "insurance_policies", "7A")
    elif section_key == "community_memberships":
        enriched = _enrich_items_in_place(result, "community_memberships", "8A")
    elif section_key == "banking_financial_accounts":
        enriched = _enrich_items_in_place(result, "banking_financial_accounts", "12A")
        enriched = _enrich_items_in_place(
            enriched, "banking_financial_accounts", "12B"
        )
    elif section_key == "passwords_online_accounts":
        enriched = _enrich_items_in_place(
            result, "passwords_online_accounts", "13A"
        )

    return recover_fields_from_notes(enriched, section_key) or enriched


def build_detected_facts_payload(
    *,
    primary_section: str,
    primary_result: dict | None,
    cached_extractions: dict | None = None,
) -> list[dict]:
    """List all temporary detected facts across primary + partner caches."""
    facts = flatten_detected_facts_from_result(
        primary_result, section_key=primary_section
    )
    seen = {
        f"{item.get('concept') or item.get('field_key')}|{(item.get('value') or '').lower()}"
        for item in facts
    }

    for section_key, result in (cached_extractions or {}).items():
        if section_key == primary_section:
            continue
        for fact in flatten_detected_facts_from_result(result, section_key=section_key):
            dedupe = (
                f"{fact.get('concept') or fact.get('field_key')}|"
                f"{(fact.get('value') or '').lower()}"
            )
            if dedupe in seen:
                continue
            seen.add(dedupe)
            facts.append(fact)

    return facts
