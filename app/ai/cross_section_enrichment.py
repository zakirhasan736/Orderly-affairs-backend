"""Share overlapping fields between Vehicles (5) and Insurance (7) for the same document."""

from __future__ import annotations

from app.ai.semantic_field_map import (
    apply_concepts_to_item,
    as_plain_text,
    collect_concepts_from_item,
    flatten_detected_facts_from_result,
)


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
        company = concepts.get("policy_company")
        number = concepts.get("policy_number")
        expiry = concepts.get("policy_expiry")

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
        "patch": {"7A": policies},
    }


def seed_vehicles_from_insurance(insurance_result: dict | None) -> dict | None:
    """Seed vehicle insurance + expiry fields from an insurance extraction."""
    insurance_result = _enrich_items_in_place(
        insurance_result, "insurance_policies", "7A"
    )
    policies = _insurance_items(insurance_result)
    if not policies:
        return None

    chosen = None
    for policy in policies:
        policy_type = (as_plain_text(policy.get("policy_type")) or "").lower()
        if policy_type == "vehicle":
            chosen = policy
            break
    if chosen is None:
        for policy in policies:
            concepts = collect_concepts_from_item(policy)
            if (
                concepts.get("policy_number")
                or concepts.get("policy_company")
                or concepts.get("policy_expiry")
            ):
                chosen = policy
                break

    if chosen is None:
        return None

    concepts = collect_concepts_from_item(chosen)
    vehicle = apply_concepts_to_item({}, concepts, "vehicles")
    if not vehicle:
        return None

    return {
        "section": "vehicles",
        "scope": "section",
        "subsection": None,
        "confidence": 0.7,
        "patch": {"5A": [vehicle]},
    }


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

    first = dict(existing_items[0]) if isinstance(existing_items[0], dict) else {}
    donor = seed_items[0] if isinstance(seed_items[0], dict) else {}
    for key, value in donor.items():
        if first.get(key) in (None, "", [], {}):
            first[key] = value

    merged_items = [first, *existing_items[1:]]
    for item in seed_items[1:]:
        if isinstance(item, dict):
            merged_items.append(item)

    merged = dict(existing)
    merged_patch = dict(existing_patch)
    merged_patch[array_key] = merged_items
    merged["patch"] = merged_patch
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

    vehicle_concepts = _first_item_concepts(vehicle_result, "5A")
    insurance_concepts = _first_item_concepts(insurance_result, "7A")

    merged_concepts: dict[str, str] = {}
    for concept in SHARED_VEHICLE_INSURANCE_CONCEPTS:
        merged_concepts[concept] = (
            insurance_concepts.get(concept)
            or vehicle_concepts.get(concept)
            or ""
        )
    merged_concepts = {k: v for k, v in merged_concepts.items() if v}

    if not merged_concepts:
        return cached

    if vehicle_result:
        vehicle_result = _enrich_items_in_place(vehicle_result, "vehicles", "5A")
        patch = dict(vehicle_result.get("patch") or {})
        items = patch.get("5A")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            items = list(items)
            items[0] = apply_concepts_to_item(items[0], merged_concepts, "vehicles")
            patch["5A"] = items
            next_vehicle = dict(vehicle_result)
            next_vehicle["patch"] = patch
            cached["vehicles"] = next_vehicle

    if insurance_result:
        insurance_result = _enrich_items_in_place(
            insurance_result, "insurance_policies", "7A"
        )
        patch = dict(insurance_result.get("patch") or {})
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
            patch["7A"] = items
            next_insurance = dict(insurance_result)
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
    if section_key == "vehicles":
        return _enrich_items_in_place(result, "vehicles", "5A")
    if section_key == "insurance_policies":
        return _enrich_items_in_place(result, "insurance_policies", "7A")
    return result


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
