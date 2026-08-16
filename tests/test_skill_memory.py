"""Skill JSON corpus for the future Orderly own model."""

from app.ai.skill_memory import (
    SKILL_SCHEMA_VERSION,
    TASK_CLASSIFY,
    TASK_FILL,
    TASK_OCR,
    build_classify_skill_record,
    build_ocr_skill_record,
    build_skill_record,
    compact_classification,
    redact_skill_text,
    summarize_patch,
)


INSURANCE_CATALOG = [
    {
        "key": "policy_company",
        "label": "Insurance Company",
        "helperText": "Carrier or insurer.",
        "type": "TextInput",
    },
    {
        "key": "policy_number",
        "label": "Policy Number",
        "helperText": "Unique policy identifier.",
        "type": "TextInput",
    },
    {
        "key": "beneficiaries",
        "label": "Beneficiaries",
        "type": "TextInput",
    },
]


def test_redact_ssn_and_card():
    text = "SSN 123-45-6789 card 4111 1111 1111 1111 policy HO-12345"
    cleaned = redact_skill_text(text)
    assert "123-45-6789" not in cleaned
    assert "[SSN]" in cleaned
    assert "[CARD]" in cleaned
    assert "HO-12345" in cleaned


def test_summarize_patch_tracks_subsection_and_fields():
    summary = summarize_patch(
        {"7A": [{"policy_company": "State Farm", "policy_number": "HO-1", "beneficiaries": ""}]},
        INSURANCE_CATALOG,
    )
    assert summary["subsections"] == ["7A"]
    assert summary["filled_field_keys"] == ["policy_company", "policy_number"]
    assert "beneficiaries" in summary["omitted_field_keys"]


def test_fill_skill_record_captures_decisions():
    doc = build_skill_record(
        user_id="u1",
        section_key="insurance_policies",
        subsection="7A",
        document_text=(
            "Homeowners Coverage. Insurance Carrier: State Farm. "
            "Policy Number: HO-927281. Named Insured: John Smith."
        ),
        patch={"7A": [{"policy_company": "State Farm", "policy_number": "HO-927281"}]},
        confidence=0.97,
        extract_meta={
            "method": "pytesseract",
            "quality": "good",
            "quality_score": 0.88,
            "needs_vision": False,
            "terra_invoked": False,
            "pipeline_path": "ocr_sol",
            "source_method": "ocr",
            "read_source": "system",
        },
        classification={
            "best_section_key": "insurance_policies",
            "matches_requested_section": True,
            "confidence": "high",
            "document_summary": "Homeowners policy from State Farm.",
            "additional_sections": [],
        },
        field_catalog=INSURANCE_CATALOG,
        result={"section": "insurance_policies", "scope": "section", "confidence": 0.97},
    )
    assert doc is not None
    assert doc["schema_version"] == SKILL_SCHEMA_VERSION
    assert doc["task"] == TASK_FILL
    behaviors = doc["behaviors"]
    assert behaviors["ocr"]["pipeline_path"] == "ocr_sol"
    assert behaviors["ocr"]["terra_invoked"] is False
    assert behaviors["classify"]["detected_section"] == "insurance_policies"
    assert behaviors["fill"]["filled_subsections"] == ["7A"]
    assert "policy_number" in behaviors["fill"]["filled_field_keys"]
    assert behaviors["fill"]["confidence_band"] == "high"
    assert behaviors["fill"]["autofill_eligible"] is True
    messages = doc["train"]["messages"]
    assert messages[0]["role"] == "system"
    assert "policy_company" in messages[1]["content"]
    assert "HO-927281" in messages[2]["content"]


def test_fill_skill_skips_empty_patch():
    assert (
        build_skill_record(
            user_id="u1",
            section_key="insurance_policies",
            document_text="Homeowners Coverage. Insurance Carrier: State Farm. Extra context here.",
            patch={},
        )
        is None
    )


def test_classify_skill_record():
    doc = build_classify_skill_record(
        user_id="u1",
        requested_section_key="vital_information",
        document_text=(
            "This is a checking account statement from Chase Bank for January 2026. "
            "Account ending 9988. Beginning balance 1200."
        ),
        classification={
            "best_section_key": "banking_financial_accounts",
            "matches_requested_section": False,
            "confidence": "high",
            "document_summary": "Chase checking statement.",
            "additional_sections": [],
        },
        extract_meta={"quality": "good", "pipeline_path": "ocr_sol", "terra_invoked": False},
    )
    assert doc is not None
    assert doc["task"] == TASK_CLASSIFY
    assert doc["output"]["best_section_key"] == "banking_financial_accounts"
    assert doc["behaviors"]["classify"]["matches_requested_section"] is False
    assert "banking_financial_accounts" in doc["train"]["messages"][2]["content"]


def test_ocr_skill_records_terra_fallback():
    doc = build_ocr_skill_record(
        user_id="u1",
        section_key="insurance_policies",
        document_text="Insurance Carrier: State Farm. Policy Number HO-1. Named Insured Jane.",
        extract_meta={
            "method": "pytesseract",
            "quality": "bad",
            "quality_score": 0.2,
            "needs_vision": True,
            "terra_invoked": True,
            "terra_pages": [1],
            "pipeline_path": "ocr_terra_sol",
            "source_method": "terra_vision",
        },
    )
    assert doc is not None
    assert doc["task"] == TASK_OCR
    assert doc["output"]["terra_invoked"] is True
    assert doc["output"]["pipeline_path"] == "ocr_terra_sol"


def test_compact_classification_keeps_section_decisions():
    compact = compact_classification(
        {
            "best_section_key": "insurance_policies",
            "matches_requested_section": True,
            "confidence": "high",
            "document_summary": "Auto card",
            "additional_sections": [
                {"section_key": "vehicles", "confidence": "medium", "data_summary": "VIN listed"}
            ],
        }
    )
    assert compact["additional_sections"][0]["section_key"] == "vehicles"
