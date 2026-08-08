"""Insurance policy duplicate matching (mirrors frontend aiItemDedup)."""

from app.ai.background_section_persist import _insurance_policies_are_duplicates


def test_merges_same_bmw_notes_with_and_without_company():
    assert _insurance_policies_are_duplicates(
        {
            "policy_type": "Vehicle",
            "notes": "Bmw Ix",
            "coverage_amount": "50k",
        },
        {
            "policy_company": "Allstate",
            "policy_type": "Vehicle",
            "notes": "Bmw",
            "premium_info": "$90",
        },
    )


def test_merges_ymm_fingerprint_with_brand_notes():
    assert _insurance_policies_are_duplicates(
        {
            "policy_company": "Allstate",
            "policy_type": "Vehicle",
            "make": "Bmw",
            "model": "Ix",
            "coverage_amount": "1",
        },
        {
            "policy_company": "Allstate",
            "policy_type": "Vehicle",
            "notes": "Bmw coverage",
            "coverage_amount": "2",
        },
    )


def test_keeps_bmw_and_kia_separate():
    assert not _insurance_policies_are_duplicates(
        {
            "policy_company": "Allstate",
            "policy_type": "Vehicle",
            "notes": "Bmw Ix",
        },
        {
            "policy_company": "Allstate",
            "policy_type": "Vehicle",
            "notes": "Kia Sorento",
        },
    )


def test_merges_homeowner_duplicates():
    assert _insurance_policies_are_duplicates(
        {
            "policy_company": "Allstate",
            "policy_type": "Homeowner/Renter",
            "coverage_amount": "300k",
        },
        {
            "policy_company": "Allstate",
            "policy_type": "Homeowner/Renter",
            "notes": "Lookout mountain",
        },
    )


def test_merges_allstate_title_noise_same_vehicle():
    assert _insurance_policies_are_duplicates(
        {
            "policy_company": "Allstate",
            "policy_type": "Vehicle",
            "policy_name": "Allstate Insurance Declarations",
            "notes": "Bmw",
            "coverage_amount": "1",
        },
        {
            "policy_company": "Allstate",
            "policy_type": "Vehicle",
            "policy_name": "Allstate Auto Policy Packet",
            "notes": "Bmw Ix",
            "coverage_amount": "2",
        },
    )


def test_merges_anonymous_same_carrier_vehicle_shells():
    assert _insurance_policies_are_duplicates(
        {
            "policy_company": "State Farm",
            "policy_type": "Vehicle",
            "coverage_amount": "50k",
            "premium_info": "$100/mo",
        },
        {
            "policy_company": "State Farm",
            "policy_type": "Vehicle",
            "coverage_amount": "60k",
            "premium_info": "$120/mo",
        },
    )


def test_does_not_absorb_full_shell_into_branded_card():
    assert not _insurance_policies_are_duplicates(
        {
            "policy_company": "Allstate",
            "policy_type": "Vehicle",
            "notes": "Bmw Ix",
            "coverage_amount": "50k",
            "premium_info": "$100",
        },
        {
            "policy_company": "Allstate",
            "policy_type": "Vehicle",
            "policy_name": "Allstate Insurance",
            "coverage_amount": "80k",
            "premium_info": "$200",
        },
    )
