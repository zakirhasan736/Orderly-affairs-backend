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
