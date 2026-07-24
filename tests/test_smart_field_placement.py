from app.ai.smart_field_placement import (
    coerce_dropdown_value,
    coerce_catalog_values,
    remap_extraction_result,
    smart_place_onto_fields,
)


def test_smart_place_banking_aliases_onto_exact_labels():
    fields = [
        {"key": "account_number", "label": "Account Number"},
        {"key": "routing_number", "label": "Routing Number"},
        {"key": "bank_name", "label": "Bank Name"},
        {"key": "notes", "label": "Notes"},
    ]

    placed = smart_place_onto_fields(
        {
            "acct_no": "123456",
            "aba": "021000021",
            "financial_institution": "Chase",
            "random_other": "keep-me",
        },
        fields,
    )

    assert placed["account_number"] == "123456"
    assert placed["routing_number"] == "021000021"
    assert placed["bank_name"] == "Chase"
    assert placed["random_other"] == "keep-me"
    assert "acct_no" not in placed


def test_remap_extraction_result_uses_catalog():
    result = {
        "section": "banking_financial_accounts",
        "patch": {
            "12A": [
                {
                    "acct_no": "999",
                    "bank": "Wells Fargo",
                }
            ]
        },
    }
    fields = [
        {"key": "account_number", "label": "Account Number"},
        {"key": "bank_name", "label": "Bank Name"},
    ]

    remapped = remap_extraction_result(result, fields)
    item = remapped["patch"]["12A"][0]
    assert item["account_number"] == "999"
    assert item["bank_name"] == "Wells Fargo"


def test_place_by_dropdown_option_value_when_key_differs():
    fields = [
        {
            "key": "policy_type",
            "label": "Policy Type",
            "type": "Dropdown",
            "options": ["Vehicle", "Life", "Health", "Homeowner/Renter"],
        },
        {"key": "policy_company", "label": "Insurance Company", "type": "TextInput"},
    ]

    placed = smart_place_onto_fields(
        {
            "coverage_kind": "Auto insurance",
            "carrier_name": "Geico",
        },
        fields,
    )

    assert placed["policy_type"] == "Auto insurance"
    assert placed["policy_company"] == "Geico"

    coerced = coerce_catalog_values(placed, fields)
    assert coerced["policy_type"] == "Vehicle"


def test_coerce_radio_checkbox_and_synonyms():
    fields = [
        {
            "key": "policy_type",
            "type": "Dropdown",
            "options": ["Vehicle", "Life", "Health"],
        },
        {
            "key": "notify",
            "type": "RadioButtons",
            "options": ["Notify organization"],
        },
        {"key": "active", "type": "Checkbox"},
    ]

    coerced = coerce_catalog_values(
        {
            "policy_type": "car",
            "notify": "yes",
            "active": "true",
        },
        fields,
    )
    assert coerced["policy_type"] == "Vehicle"
    assert coerced["notify"] == "Notify organization"
    assert coerced["active"] is True


def test_coerce_dropdown_synonym_homeowners():
    assert (
        coerce_dropdown_value(
            "Homeowners policy",
            ["Vehicle", "Life", "Homeowner/Renter", "Health"],
        )
        == "Homeowner/Renter"
    )
