from app.ai.cross_section_enrichment import (
    merge_seed_into_cached,
    seed_insurance_from_vehicles,
    seed_vehicles_from_insurance,
)
from app.ai.semantic_field_map import (
    collect_concepts_from_item,
    extract_end_date_from_text,
    resolve_concept_from_key,
)


def test_seed_insurance_from_vehicles_copies_policy_fields():
    vehicle_result = {
        "patch": {
            "5A": [
                {
                    "year": "2020",
                    "make": "Honda",
                    "model": "Civic",
                    "insurance_company": "State Farm",
                    "insurance_policy": "POL-123",
                    "registration_expiry": "2025-12-31",
                }
            ]
        }
    }

    seeded = seed_insurance_from_vehicles(vehicle_result)
    assert seeded is not None
    policy = seeded["patch"]["7A"][0]
    assert policy["policy_company"] == "State Farm"
    assert policy["policy_number"] == "POL-123"
    assert policy["policy_expiry"] == "2025-12-31"
    assert policy["policy_type"] == "Vehicle"


def test_seed_vehicles_from_insurance_copies_policy_fields():
    insurance_result = {
        "patch": {
            "7A": [
                {
                    "policy_type": "Vehicle",
                    "policy_company": "Geico",
                    "policy_number": "G-99",
                    "policy_expiry": "01/15/2026",
                }
            ]
        }
    }

    seeded = seed_vehicles_from_insurance(insurance_result)
    assert seeded is not None
    vehicle = seeded["patch"]["5A"][0]
    assert vehicle["insurance_company"] == "Geico"
    assert vehicle["insurance_policy"] == "G-99"
    assert vehicle["registration_expiry"] == "2026-01-15"


def test_seed_vehicles_from_insurance_parses_multi_vehicle_notes():
    insurance_result = {
        "patch": {
            "7A": [
                {
                    "policy_type": "Vehicle",
                    "policy_company": "State Farm",
                    "policy_number": "SF-100",
                    "policy_expiry": "2026-06-30",
                    "notes": (
                        "Vehicle: 2020 Honda Civic; VIN: 1HGBH41JXMN109186; Plate: ABC123; "
                        "Vehicle: 2018 Toyota Camry; VIN: 4T1B11HK5JU123456; Plate: XYZ789"
                    ),
                }
            ]
        }
    }

    seeded = seed_vehicles_from_insurance(insurance_result)
    assert seeded is not None
    vehicles = seeded["patch"]["5A"]
    assert len(vehicles) == 2
    assert vehicles[0]["make"] == "Honda"
    assert vehicles[0]["model"] == "Civic"
    assert vehicles[0]["year"] == "2020"
    assert vehicles[0]["vin"] == "1HGBH41JXMN109186"
    assert vehicles[0]["insurance_policy"] == "SF-100"
    assert vehicles[1]["make"] == "Toyota"
    assert vehicles[1]["model"] == "Camry"
    assert vehicles[1]["vin"] == "4T1B11HK5JU123456"
    assert vehicles[1]["insurance_company"] == "State Farm"


def test_seed_vehicles_skips_life_policies():
    insurance_result = {
        "patch": {
            "7A": [
                {
                    "policy_type": "Life",
                    "policy_company": "MetLife",
                    "policy_number": "L-1",
                    "notes": "Term life for family",
                }
            ]
        }
    }
    assert seed_vehicles_from_insurance(insurance_result) is None


def test_merge_seed_keeps_distinct_vehicles_on_same_policy():
    existing = {
        "extraction_source": "llm",
        "patch": {
            "5A": [
                {
                    "year": "2020",
                    "make": "Honda",
                    "model": "Civic",
                    "insurance_policy": "SF-100",
                }
            ]
        },
    }
    seed = {
        "extraction_source": "cross_seed",
        "patch": {
            "5A": [
                {
                    "year": "2018",
                    "make": "Toyota",
                    "model": "Camry",
                    "insurance_policy": "SF-100",
                    "insurance_company": "State Farm",
                }
            ]
        },
    }
    merged = merge_seed_into_cached(existing, seed, array_key="5A")
    assert len(merged["patch"]["5A"]) == 2
    assert merged["extraction_source"] == "llm"


def test_semantic_aliases_and_period_end_date():
    assert resolve_concept_from_key("member_id") == "policy_number"
    assert resolve_concept_from_key("valid_through") == "policy_expiry"
    assert (
        extract_end_date_from_text("Policy period 01/01/2025 to 12/31/2025")
        == "2025-12-31"
    )
    assert (
        extract_end_date_from_text(
            "Valid from January 1, 2025 through December 31, 2025"
        )
        == "2025-12-31"
    )
    assert (
        extract_end_date_from_text("Coverage 2025-01-01 to 2025-12-31")
        == "2025-12-31"
    )

    concepts = collect_concepts_from_item(
        {
            "premium_info": "Policy period 03/01/2024 through 02/28/2025",
            "insurance_policy": "ABC-1",
        }
    )
    assert concepts["policy_number"] == "ABC-1"
    assert concepts["policy_expiry"] == "2025-02-28"

    # Expiry field holding a range should keep the end date.
    concepts2 = collect_concepts_from_item(
        {"policy_expiry": "01/01/2025 - 12/31/2025", "notes": "auto"}
    )
    assert concepts2["policy_expiry"] == "2025-12-31"


def test_insurance_number_alias_and_bidirectional_sync():
    assert resolve_concept_from_key("insurance_number") == "policy_number"
    assert resolve_concept_from_key("Insurance Number") == "policy_number"

    from app.ai.cross_section_enrichment import sync_vehicle_insurance_shared_fields

    cached = {
        "vehicles": {
            "patch": {
                "5A": [
                    {
                        "license_plate": "ABC-1",
                        "insurance_policy": "POL-999",
                        "insurance_company": "Geico",
                    }
                ]
            }
        },
        "insurance_policies": {
            "patch": {
                "7A": [
                    {
                        "policy_type": "Vehicle",
                        "policy_company": "Geico",
                        # policy_number intentionally missing — sync should fill it
                    }
                ]
            }
        },
    }

    synced = sync_vehicle_insurance_shared_fields(cached)
    assert synced["insurance_policies"]["patch"]["7A"][0]["policy_number"] == "POL-999"
    assert synced["vehicles"]["patch"]["5A"][0]["insurance_policy"] == "POL-999"


def test_merge_seed_fills_empty_fields_only():
    existing = {
        "patch": {
            "7A": [
                {
                    "policy_company": "State Farm",
                    "policy_number": None,
                    "coverage_amount": "100000",
                }
            ]
        }
    }
    seed = {
        "patch": {
            "7A": [
                {
                    "policy_company": "Other",
                    "policy_number": "POL-1",
                    "coverage_amount": "999",
                }
            ]
        }
    }

    merged = merge_seed_into_cached(existing, seed, array_key="7A")
    item = merged["patch"]["7A"][0]
    assert item["policy_company"] == "State Farm"
    assert item["policy_number"] == "POL-1"
    assert item["coverage_amount"] == "100000"
