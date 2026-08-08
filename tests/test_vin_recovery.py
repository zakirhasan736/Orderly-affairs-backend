from app.ai.cross_section_enrichment import enrich_primary_result, seed_vehicles_from_insurance
from app.ai.vin_utils import (
    find_vins_in_text,
    normalize_vin,
    recover_vins_for_vehicle_result,
)


def test_find_vins_labeled_and_spaced():
    text = "2025 BMW Ix  VIN: 5YJ3E1EA1KF123456  Plate: ABC123"
    assert find_vins_in_text(text) == ["5YJ3E1EA1KF123456"]

    spaced = "Veh ID 1HG BH4 1JX MN1 09186"
    assert normalize_vin("1HG BH4 1JX MN1 09186") == "1HGBH41JXMN109186"
    assert find_vins_in_text(spaced) == ["1HGBH41JXMN109186"]


def test_find_standalone_vin():
    text = "Unit 1 2025 BMW Ix 5YJ3E1EA1KF654321 Allstate"
    assert "5YJ3E1EA1KF654321" in find_vins_in_text(text)


def test_recover_vins_from_document_text_onto_vehicles():
    result = {
        "section": "vehicles",
        "patch": {
            "5A": [
                {"year": "2025", "make": "BMW", "model": "Ix"},
                {"year": "2015", "make": "Kia", "model": "Sorento"},
            ]
        },
    }
    doc_text = (
        "Vehicle Schedule\n"
        "2025 BMW Ix VIN 5YJ3E1EA1KF111111\n"
        "2015 Kia Sorento VIN 5XYKT3A69FG222222\n"
    )
    recovered = recover_vins_for_vehicle_result(result, doc_text)
    vehicles = recovered["patch"]["5A"]
    assert vehicles[0]["vin"] == "5YJ3E1EA1KF111111"
    assert vehicles[1]["vin"] == "5XYKT3A69FG222222"


def test_enrich_primary_result_fills_vin_from_document_text():
    result = {
        "section": "vehicles",
        "patch": {
            "5A": [
                {
                    "year": "2025",
                    "make": "BMW",
                    "model": "Ix",
                    "insurance_company": "Allstate",
                }
            ]
        },
    }
    enriched = enrich_primary_result(
        result,
        "vehicles",
        document_text="2025 BMW Ix Vehicle Identification Number: WBY7Z4C59NWM33333",
    )
    assert enriched["patch"]["5A"][0]["vin"] == "WBY7Z4C59NWM33333"


def test_seed_vehicles_from_insurance_still_parses_vin_lines():
    insurance_result = {
        "patch": {
            "7A": [
                {
                    "policy_type": "Vehicle",
                    "policy_company": "Allstate",
                    "policy_number": "POL-1",
                    "notes": (
                        "Vehicle: 2025 BMW Ix; VIN: 5YJ3E1EA1KF444444; "
                        "Vehicle: 2015 Kia Sorento; VIN: 5XYKT3A69FG555555"
                    ),
                }
            ]
        }
    }
    seeded = seed_vehicles_from_insurance(insurance_result)
    vins = {item.get("vin") for item in seeded["patch"]["5A"]}
    assert "5YJ3E1EA1KF444444" in vins
    assert "5XYKT3A69FG555555" in vins


def test_enrich_recovers_vehicles_from_empty_patch_using_document_text():
    """Insurance cards sometimes return empty 5A — still build cards from OCR text."""
    result = {"section": "vehicles", "patch": {"5A": []}}
    doc_text = (
        "Allstate Auto\n"
        "Vehicle: 2025 BMW Ix VIN: 5YJ3E1EA1KF666666\n"
        "Vehicle: 2015 Kia Sorento VIN: 5XYKT3A69FG777777\n"
    )
    enriched = enrich_primary_result(result, "vehicles", document_text=doc_text)
    vehicles = enriched["patch"]["5A"]
    assert len(vehicles) >= 2
    vins = {item.get("vin") for item in vehicles}
    assert "5YJ3E1EA1KF666666" in vins
    assert "5XYKT3A69FG777777" in vins
    makes = {(item.get("make") or "").lower() for item in vehicles}
    assert "bmw" in makes
    assert "kia" in makes


def test_recover_vins_creates_rows_when_5a_empty():
    result = {"section": "vehicles", "patch": {"5A": []}}
    recovered = recover_vins_for_vehicle_result(
        result,
        "VIN: 5YJ3E1EA1KF888888  VIN: 5XYKT3A69FG999999",
    )
    vehicles = recovered["patch"]["5A"]
    assert len(vehicles) == 2
    assert vehicles[0]["vin"] == "5YJ3E1EA1KF888888"
    assert vehicles[1]["vin"] == "5XYKT3A69FG999999"