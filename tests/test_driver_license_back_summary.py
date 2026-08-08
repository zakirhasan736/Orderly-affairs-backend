from app.ai.document_classifier import correct_identity_document_summary


def test_texas_dl_back_not_roadside_assistance_card():
    classification = {
        "best_section_key": "vital_information",
        "confidence": "high",
        "matches_requested_section": True,
        "document_summary": (
            "This document is a Texas Roadside Assistance card. "
            "It includes a date of birth (09/15/1978) and a unique identification number. "
            "The card is useful for verifying personal identity and emergency contact information."
        ),
        "additional_sections": [],
    }
    doc_text = (
        "CLASS: C REST: NONE END: NONE "
        "DOB: 09/15/1978 "
        "TEXAS ROADSIDE ASSISTANCE: 1-800-525-5555"
    )
    fixed = correct_identity_document_summary(classification, doc_text)
    summary = fixed["document_summary"].lower()
    assert "driver" in summary or "license" in summary or "photo id" in summary
    assert "this document is a texas roadside assistance card" not in summary
    assert "help line" in summary or "printed" in summary
    assert "09/15/1978" in fixed["document_summary"]
    assert summary.startswith("this is the back of a state")

def test_real_roadside_card_without_dl_cues_unchanged():
    classification = {
        "best_section_key": "community_memberships",
        "document_summary": (
            "This document is a roadside assistance membership card from AAA "
            "with member number 12345."
        ),
        "additional_sections": [],
    }
    fixed = correct_identity_document_summary(
        classification,
        "AAA roadside assistance membership card member 12345",
    )
    assert "AAA" in fixed["document_summary"]
    assert "membership" in fixed["document_summary"].lower()
