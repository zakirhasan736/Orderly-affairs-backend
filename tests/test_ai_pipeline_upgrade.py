"""Document auto-fill upgrade: OCR gate, Terra/Sol routing, semantic mapping."""

from __future__ import annotations

from pathlib import Path

from app.ai.extractors.base_extractor import (
    _merge_pass_b_into,
    merge_without_overwriting_existing,
)
from app.ai.local_document_extract import (
    _score_text_quality,
    clear_prepare_cache,
    prepare_document_for_sol,
)
from app.ai.semantic_field_map import resolve_concept_from_key
from app.ai.smart_field_placement import remap_extraction_result, smart_place_onto_fields


INSURANCE_CATALOG = [
    {
        "key": "policy_company",
        "label": "Insurance Company",
        "helperText": "Insurance company, insurer, carrier or insurance provider.",
        "type": "TextInput",
    },
    {
        "key": "policy_number",
        "label": "Policy Number",
        "helperText": "Unique policy, certificate or contract identifier.",
        "type": "TextInput",
    },
    {
        "key": "member_name",
        "label": "Member Name",
        "helperText": "Named insured / policy holder / insured person.",
        "type": "TextInput",
    },
    {
        "key": "policy_expiry",
        "label": "Policy Expiry",
        "helperText": "Date the policy expires or coverage ends.",
        "type": "TextInput",
    },
    {
        "key": "policy_contact",
        "label": "Agent / contact",
        "helperText": "Agent, broker, or customer service contact.",
        "type": "TextInput",
    },
    {
        "key": "beneficiaries",
        "label": "Beneficiaries",
        "helperText": "Beneficiary names.",
        "type": "TextInput",
    },
]


def _place(incoming: dict) -> dict:
    return smart_place_onto_fields(incoming, INSURANCE_CATALOG)


def test_exact_policy_number_label():
    placed = _place({"policy_number": "HO-12345"})
    assert placed["policy_number"] == "HO-12345"


def test_abbreviation_policy_no():
    placed = _place({"policy_no": "HO-12345"})
    assert placed["policy_number"] == "HO-12345"


def test_ocr_misspelling_polcy_numbor():
    assert resolve_concept_from_key("Polcy Numbor") == "policy_number"
    placed = _place({"polcy_numbor": "HO-12345"})
    assert placed["policy_number"] == "HO-12345"


def test_semantic_insurance_carrier():
    placed = _place({"insurance_carrier": "State Farm"})
    assert placed["policy_company"] == "State Farm"


def test_insurance_name_alias():
    placed = _place({"insurance_name": "State Farm"})
    assert placed["policy_company"] == "State Farm"


def test_named_insured_maps_to_member_name():
    placed = _place({"named_insured": "John Smith"})
    assert placed["member_name"] == "John Smith"


def test_missing_policy_number_not_fabricated():
    placed = _place({"policy_company": "State Farm", "member_name": "John Smith"})
    assert not placed.get("policy_number")


def test_ambiguous_dates_stay_distinct():
    placed = _place(
        {
            "effective_date": "2026-01-01",
            "expiration_date": "2027-01-01",
            "renewal_date": "2026-12-15",
        }
    )
    assert placed.get("policy_expiry") == "2027-01-01"
    assert placed.get("renewal_date") == "2026-12-15"
    assert placed.get("effective_date") == "2026-01-01"
    assert placed.get("policy_expiry") != placed.get("renewal_date")
    assert placed.get("policy_expiry") != placed.get("effective_date")


def test_multiple_people_map_to_distinct_fields():
    placed = _place(
        {
            "policy_holder": "John Smith",
            "agent": "Jane Smith",
            "beneficiary": "Robert Smith",
        }
    )
    assert placed.get("member_name") == "John Smith"
    assert placed.get("policy_contact") == "Jane Smith"
    assert placed.get("beneficiaries") == "Robert Smith"


def test_bank_notes_do_not_fill_insurance_policy_number():
    result = {
        "section": "insurance_policies",
        "confidence": 0.9,
        "patch": {
            "7A": [
                {
                    "notes": (
                        "Chase checking statement. Optional add-on mentions insurance. "
                        "Routing 021000021. Account 998877."
                    )
                }
            ]
        },
    }
    remapped = remap_extraction_result(result, INSURANCE_CATALOG)
    item = remapped["patch"]["7A"][0]
    assert not item.get("policy_number")
    assert not item.get("policy_company")


def test_existing_value_not_overwritten():
    existing = {"policy_number": "USER-111", "policy_company": ""}
    incoming = {"policy_number": "AI-999", "policy_company": "State Farm"}
    merged = merge_without_overwriting_existing(existing, incoming)
    assert merged["policy_number"] == "USER-111"
    assert merged["policy_company"] == "State Farm"


def test_pass_b_does_not_overwrite_filled_fields():
    primary = {
        "patch": {"7A": [{"policy_number": "HO-1", "policy_company": ""}]},
        "confidence": 0.9,
    }
    secondary = {
        "patch": {"7A": [{"policy_number": "HO-OTHER", "policy_company": "State Farm"}]},
        "confidence": 0.8,
    }
    merged = _merge_pass_b_into(primary, secondary)
    assert merged["patch"]["7A"][0]["policy_number"] == "HO-1"
    assert merged["patch"]["7A"][0]["policy_company"] == "State Farm"


def test_garbage_ocr_is_bad_quality():
    score, needs_vision, quality = _score_text_quality(
        "@@@ ### ||| ~~~~ xkqp zz ~~ \\/\\/~^",
        min_chars=20,
    )
    assert needs_vision is True
    assert quality == "bad"
    assert score < 0.5


def test_good_ocr_plain_text_skips_terra(tmp_path: Path, monkeypatch):
    clear_prepare_cache()
    path = tmp_path / "policy.txt"
    path.write_text(
        "Homeowners Insurance Policy\n"
        "Insurance Carrier: State Farm\n"
        "Policy Number: HO-12345\n"
        "Named Insured: John Smith\n"
        "Coverage Ends: 09/20/2027\n" * 2,
        encoding="utf-8",
    )
    called = {"terra": 0}

    def fake_terra(*_args, **_kwargs):
        called["terra"] += 1
        return {"text": "SHOULD NOT RUN", "uncertain": False, "usage": None}

    monkeypatch.setattr(
        "app.ai.local_document_extract.terra_read_vision_parts",
        fake_terra,
    )
    meta = prepare_document_for_sol(path, "text/plain")
    assert called["terra"] == 0
    assert meta["terra_invoked"] is False
    assert "HO-12345" in meta["text"]
    assert meta.get("pipeline_path") == "ocr_sol"


def test_bad_ocr_invokes_terra(tmp_path: Path, monkeypatch):
    clear_prepare_cache()
    path = tmp_path / "scan.png"
    path.write_bytes(b"fake-image")

    def fake_extract(_path, _mime):
        return {
            "text": "@@@ ### ||| ~~~~ xkqp zz",
            "method": "pytesseract",
            "quality_score": 0.12,
            "needs_vision": True,
            "quality": "bad",
            "source": "ocr",
            "pages": [
                {
                    "page": 1,
                    "text": "@@@ ### |||",
                    "needs_vision": True,
                    "quality": "bad",
                    "quality_score": 0.12,
                }
            ],
            "terra_invoked": False,
            "reader": "none",
            "page_count": 1,
        }

    called = {"terra": 0}

    def fake_terra(*_args, **_kwargs):
        called["terra"] += 1
        return {
            "text": "Insurance Carrier: State Farm\nPolicy Number: HO-12345",
            "uncertain": False,
            "usage": {"total_tokens": 12, "estimated_usd": 0.001},
        }

    monkeypatch.setattr(
        "app.ai.local_document_extract.extract_document_text", fake_extract
    )
    monkeypatch.setattr(
        "app.ai.local_document_extract.terra_read_vision_parts", fake_terra
    )
    monkeypatch.setattr(
        "app.ai.local_document_extract._vision_parts_for_file",
        lambda *_args, **_kwargs: [
            {"type": "image", "mime_type": "image/png", "data_b64": "xx"}
        ],
    )
    monkeypatch.setattr(
        "app.ai.local_document_extract.vision_fallback_enabled", lambda: True
    )

    meta = prepare_document_for_sol(path, "image/png")
    assert called["terra"] == 1
    assert meta["terra_invoked"] is True
    assert "State Farm" in meta["text"]
    assert meta.get("pipeline_path") == "ocr_terra_sol"
    assert meta.get("llm_input") == "text"


def test_good_ocr_image_skips_terra(tmp_path: Path, monkeypatch):
    clear_prepare_cache()
    path = tmp_path / "card.png"
    path.write_bytes(b"fake-image")
    readable = (
        "Auto insurance card for Jane Doe. Policy number ABC-12345. "
        "Vehicle: 2020 Honda Civic. Insurance Carrier State Farm."
    )

    def fake_extract(_path, _mime):
        return {
            "text": readable,
            "method": "pytesseract",
            "quality_score": 0.82,
            "needs_vision": False,
            "quality": "good",
            "source": "ocr",
            "pages": [
                {
                    "page": 1,
                    "text": readable,
                    "needs_vision": False,
                    "quality": "good",
                    "quality_score": 0.82,
                }
            ],
            "terra_invoked": False,
            "reader": "system",
            "page_count": 1,
        }

    called = {"terra": 0}

    def fake_terra(*_args, **_kwargs):
        called["terra"] += 1
        return {"text": "NO", "uncertain": False, "usage": None}

    monkeypatch.setattr(
        "app.ai.local_document_extract.extract_document_text", fake_extract
    )
    monkeypatch.setattr(
        "app.ai.local_document_extract.terra_read_vision_parts", fake_terra
    )
    monkeypatch.setattr(
        "app.ai.local_document_extract.vision_fallback_enabled", lambda: True
    )

    meta = prepare_document_for_sol(path, "image/png")
    assert called["terra"] == 0
    assert meta["terra_invoked"] is False
    assert "ABC-12345" in meta["text"]
