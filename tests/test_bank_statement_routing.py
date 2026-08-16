from app.ai.document_classifier import (
    harden_bank_statement_routing,
    harden_vehicle_insurance_routing,
)


def test_bank_statement_not_routed_to_residence():
    classification = {
        "best_section_key": "main_residence",
        "confidence": "high",
        "matches_requested_section": True,
        "document_summary": (
            "This is a monthly bank statement from Lakeshore National Bank "
            "for the account holders Jordan Michael Casey and Alexis Renee Casey. "
            "The statement covers June 1, 2026 to June 30, 2026 and can be used "
            "to fill banking and financial account details."
        ),
        "additional_sections": [
            {
                "section_key": "banking_financial_accounts",
                "confidence": "high",
                "data_summary": "Checking account details",
            }
        ],
    }
    doc_text = (
        "Lakeshore National Bank Monthly Statement "
        "Beginning balance $4,210.18  Ending balance $3,887.42 "
        "4709 Lookout Mountain Cv, Austin, TX 78731"
    )
    fixed = harden_vehicle_insurance_routing(classification, doc_text)
    assert fixed["best_section_key"] == "banking_financial_accounts"
    extra = {
        item.get("section_key")
        for item in (fixed.get("additional_sections") or [])
        if isinstance(item, dict)
    }
    assert "main_residence" not in extra


def test_mortgage_statement_keeps_residence_and_banking():
    classification = {
        "best_section_key": "main_residence",
        "confidence": "high",
        "document_summary": (
            "This is a mortgage statement from Lakeshore National Bank "
            "for the home at 4709 Lookout Mountain Cv."
        ),
        "additional_sections": [],
    }
    fixed = harden_bank_statement_routing(classification)
    assert fixed["best_section_key"] == "main_residence"
    extra = {
        item.get("section_key")
        for item in (fixed.get("additional_sections") or [])
        if isinstance(item, dict)
    }
    assert "banking_financial_accounts" in extra


def test_auto_insurance_card_does_not_route_to_health():
    classification = {
        "best_section_key": "insurance_policies",
        "confidence": "high",
        "matches_requested_section": True,
        "document_summary": (
            "This is an auto insurance identification card for a Honda CR-V. "
            "It lists the policy number and vehicle details."
        ),
        "additional_sections": [
            {
                "section_key": "health_information",
                "confidence": "medium",
                "data_summary": "Insurance name for health",
            },
            {
                "section_key": "vehicles",
                "confidence": "high",
                "data_summary": "Honda CR-V vehicle details",
            },
        ],
    }
    doc_text = (
        "AUTO INSURANCE IDENTIFICATION CARD Honda CR-V "
        "VIN 1HGCM82633A004352 Policy 123456789"
    )
    fixed = harden_vehicle_insurance_routing(classification, doc_text)
    extra = {
        item.get("section_key")
        for item in (fixed.get("additional_sections") or [])
        if isinstance(item, dict)
    }
    assert fixed["best_section_key"] in {"insurance_policies", "vehicles"}
    assert "health_information" not in extra
    assert "health_information" in (fixed.get("skip_section_keys") or [])
    assert extra <= {"vehicles", "insurance_policies"}


def test_paystub_does_not_route_to_insurance():
    classification = {
        "best_section_key": "insurance_policies",
        "confidence": "medium",
        "document_summary": "This is an employee pay stub showing gross and net pay.",
        "additional_sections": [
            {
                "section_key": "insurance_policies",
                "confidence": "low",
                "data_summary": "Benefits",
            }
        ],
    }
    doc_text = "Pay stub Gross pay $4200 Net pay $3100 Earnings statement"
    fixed = harden_vehicle_insurance_routing(classification, doc_text)
    assert fixed["best_section_key"] == "employment_business"
    extra = {
        item.get("section_key")
        for item in (fixed.get("additional_sections") or [])
        if isinstance(item, dict)
    }
    assert "insurance_policies" not in extra
    assert "insurance_policies" in (fixed.get("skip_section_keys") or [])


def test_health_card_keeps_healthcare_not_vehicles():
    classification = {
        "best_section_key": "insurance_policies",
        "confidence": "high",
        "document_summary": "UnitedHealthcare member ID card with RxBIN and group number.",
        "additional_sections": [
            {
                "section_key": "vehicles",
                "confidence": "low",
                "data_summary": "Insurance",
            }
        ],
    }
    doc_text = "UnitedHealthcare Member ID 123 Group Number 456 RxBIN 610279"
    fixed = harden_vehicle_insurance_routing(classification, doc_text)
    extra = {
        item.get("section_key")
        for item in (fixed.get("additional_sections") or [])
        if isinstance(item, dict)
    }
    assert "vehicles" not in extra
    assert "health_information" in extra
    assert "vehicles" in (fixed.get("skip_section_keys") or [])
