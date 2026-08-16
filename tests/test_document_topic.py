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
    assert old["kind"] == "insurance"
    assert new["kind"] == "insurance"
    assert old["make"] == "jeep"
    assert new["make"] == "jeep"
    assert fingerprints_match(old, new)


def test_jeep_insurance_does_not_replace_jeep_registration():
    insurance = fingerprint_from_parts(filename="Jeep_Insurance.pdf")
    registration = fingerprint_from_parts(
        filename="Vehicle_Registration_Jeep_Wrangler.pdf"
    )
    assert insurance["kind"] == "insurance"
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
    assert detect_kind("Auto policy declarations page", section_key="vehicles") == "insurance"
