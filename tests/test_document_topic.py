from app.ai.document_topic import (
    detect_kind,
    fingerprint_from_parts,
    fingerprints_match,
    normalize_filename_stem,
)


def test_filename_stem_strips_copy_suffix():
    assert normalize_filename_stem("Jeep_Insurance (1).pdf") == "jeep insurance"
    assert normalize_filename_stem("Jeep_Insurance.pdf") == "jeep insurance"


def test_jeep_insurance_matches_renamed_jeep_policy():
    old = fingerprint_from_parts(filename="Jeep_Wrangler_Insurance.pdf")
    new = fingerprint_from_parts(filename="Geico_Jeep_Policy_2026.pdf")
    assert old["kind"] in {"insurance", "auto_insurance"}
    assert new["kind"] in {"insurance", "auto_insurance"}
    assert old["make"] == "jeep"
    assert new["make"] == "jeep"
    assert fingerprints_match(old, new)


def test_jeep_insurance_does_not_replace_jeep_registration():
    insurance = fingerprint_from_parts(filename="Jeep_Insurance.pdf")
    registration = fingerprint_from_parts(
        filename="Vehicle_Registration_Jeep_Wrangler.pdf"
    )
    assert insurance["kind"] in {"insurance", "auto_insurance"}
    assert registration["kind"] == "registration"
    assert not fingerprints_match(insurance, registration)


def test_honda_insurance_does_not_replace_jeep_insurance():
    honda = fingerprint_from_parts(filename="Honda_CRV_Insurance.pdf")
    jeep = fingerprint_from_parts(filename="Jeep_Insurance.pdf")
    assert not fingerprints_match(honda, jeep)


def test_vin_match_overrides_filename():
    left = fingerprint_from_parts(
        filename="scan001.pdf",
        fields={"vin": "1C4HJXDG0JW123456", "make": "Jeep"},
        section_key="insurance_policies",
        summary="Auto insurance card for a Jeep Wrangler",
    )
    right = fingerprint_from_parts(
        filename="policy-photo.png",
        fields={"vin": "1C4HJXDG0JW123456"},
        section_key="insurance_policies",
        summary="Jeep insurance declaration",
    )
    assert fingerprints_match(left, right)


def test_conflicting_years_keep_both():
    older = fingerprint_from_parts(filename="2017_Honda_Civic_Insurance.pdf")
    newer = fingerprint_from_parts(filename="2022_Honda_Civic_Insurance.pdf")
    assert not fingerprints_match(older, newer)


def test_detect_kind_from_summary():
    assert detect_kind("Auto policy declarations page", section_key="vehicles") == "auto_insurance"


def test_auto_id_card_is_not_health_insurance():
    kind = detect_kind(
        "Auto_Insurance_ID_Card_Honda_CRV_SAMPLE.png",
        "Auto insurance identification card for a Honda CR-V",
    )
    assert kind == "auto_insurance"
    from app.ai.document_topic import skip_sections_for_kind

    assert "health_information" in skip_sections_for_kind(kind)


def test_health_card_is_not_vehicle_insurance():
    kind = detect_kind(
        "UnitedHealthcare member ID card RxBIN 610279",
        section_key="insurance_policies",
    )
    assert kind == "health_insurance"
    from app.ai.document_topic import skip_sections_for_kind

    assert "vehicles" in skip_sections_for_kind(kind)


def test_paystub_is_employment_not_insurance():
    kind = detect_kind("Employee pay stub Gross pay $4,200 Net pay $3,100")
    assert kind == "paystub"
    from app.ai.document_topic import fill_sections_for_kind, skip_sections_for_kind

    assert fill_sections_for_kind(kind)[0] == "employment_business"
    assert "insurance_policies" in skip_sections_for_kind(kind)


def test_diploma_is_education_not_employment():
    kind = detect_kind("Bachelor of Science diploma University of Texas")
    assert kind == "diploma"
    from app.ai.document_topic import skip_sections_for_kind

    assert "employment_business" in skip_sections_for_kind(kind)


def test_bank_statement_kind_skips_residence():
    kind = detect_kind(
        "Monthly bank statement checking account beginning balance ending balance"
    )
    assert kind == "bank"
    from app.ai.document_topic import skip_sections_for_kind

    assert "main_residence" in skip_sections_for_kind(kind)


def test_auto_insurance_does_not_replace_health_insurance():
    auto = fingerprint_from_parts(
        filename="Auto_Insurance_ID_Card_Honda_CRV_SAMPLE.png",
        summary="Auto insurance card for a Honda CR-V",
    )
    health = fingerprint_from_parts(
        filename="UHC_health_insurance_card.png",
        summary="UnitedHealthcare member ID card with RxBIN",
    )
    assert auto["kind"] == "auto_insurance"
    assert health["kind"] == "health_insurance"
    assert not fingerprints_match(auto, health)
