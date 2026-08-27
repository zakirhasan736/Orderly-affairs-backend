from app.ai.cross_section_enrichment import enrich_primary_result
from app.ai.drivers_license_utils import (
    harvest_drivers_license_fields,
    recover_drivers_license_for_vital_result,
)


def test_harvest_texas_style_license_fields():
    text = (
        "TEXAS DRIVER LICENSE\n"
        "DL 12345678\n"
        "DD 00\n"
        "CLASS: C\n"
        "ISS: 11/10/2020\n"
        "EXP: 09/15/2030\n"
        "DOB: 09/15/1978\n"
    )
    found = harvest_drivers_license_fields(text)
    assert found["drivers_license_number"] == "12345678"
    assert found["drivers_license_dd_number"] == "00"
    assert found["drivers_license_class"] == "C"
    assert found["drivers_license_issue_date"] == "2020-11-10"
    assert found["drivers_license_expiration_date"] == "2030-09-15"
    assert found["date_of_birth"] == "1978-09-15"


def test_recover_prefers_labeled_dob_over_prior_vital():
    result = {
        "section": "vital_information",
        "patch": {
            "vital_info": {
                "full_legal_name": "Sebastian Shahvandi",
                # Off-by-one / wrong prior fill — labeled DOB must win.
                "date_of_birth": "1978-09-14",
            }
        },
    }
    doc = (
        "DL #: 99887766  DD: 12  CLASS C  "
        "ISS 11/10/2020  EXP 09/15/2030  DOB: 09/15/1978"
    )
    recovered = recover_drivers_license_for_vital_result(result, doc)
    vital = recovered["patch"]["vital_info"]
    assert vital["date_of_birth"] == "1978-09-15"
    assert vital["drivers_license_number"] == "99887766"
    assert vital["drivers_license_expiration_date"] == "2030-09-15"


def test_recover_fills_empty_vital_info_from_document_text():
    result = {
        "section": "vital_information",
        "patch": {
            "vital_info": {
                "full_legal_name": "Sebastian Shahvandi",
                "date_of_birth": "1978-09-15",
            }
        },
    }
    doc = (
        "DL #: 99887766  DD: 12  CLASS C  "
        "ISS 11/10/2020  EXP 09/15/2030"
    )
    recovered = recover_drivers_license_for_vital_result(result, doc)
    vital = recovered["patch"]["vital_info"]
    assert vital["drivers_license_number"] == "99887766"
    assert vital["drivers_license_dd_number"] == "12"
    assert vital["drivers_license_class"] == "C"
    assert vital["drivers_license_issue_date"] == "2020-11-10"
    assert vital["drivers_license_expiration_date"] == "2030-09-15"
    assert vital["date_of_birth"] == "1978-09-15"


def test_enrich_primary_result_vital_recovers_dl_fields():
    result = {"section": "vital_information", "patch": {"vital_info": {}}}
    enriched = enrich_primary_result(
        result,
        "vital_information",
        document_text="License Number: AB1234567 CLASS: C EXP: 01/01/2031 ISS: 01/01/2021 DD 5",
    )
    vital = enriched["patch"]["vital_info"]
    assert vital.get("drivers_license_number")
    assert vital.get("drivers_license_class") == "C"
    assert vital.get("drivers_license_expiration_date") == "2031-01-01"
    assert vital.get("drivers_license_issue_date") == "2021-01-01"


def test_recover_replaces_barcode_glued_dd_number():
    result = {
        "section": "vital_information",
        "patch": {
            "vital_info": {
                "drivers_license_dd_number": "81629081015120980843",
            }
        },
    }
    recovered = recover_drivers_license_for_vital_result(
        result,
        "DL 192548900  DD 81629081  CLASS C  ISS 11/10/2020  EXP 09/13/2030",
    )
    vital = recovered["patch"]["vital_info"]
    assert vital["drivers_license_dd_number"] == "81629081"
