SECTION14_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["investment_accounts"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "14A"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "14A": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "account_type": {"type": ["string", "null"]},
                            "account_type_other": {"type": ["string", "null"]},
                            "financial_institution": {"type": ["string", "null"]},
                            "account_number": {"type": ["string", "null"]},
                            "account_value": {"type": ["string", "null"]},
                            "beneficiaries": {"type": ["string", "null"]},
                            "advisor_contact": {"type": ["string", "null"]},
                            "employer_connection": {"type": ["string", "null"]},
                            "login_credentials": {"type": ["string", "null"]},
                            "distribution_instructions": {"type": ["string", "null"]},
                            "account_documents": {"type": ["string", "null"]},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
    },
    "required": ["section", "scope", "subsection", "confidence", "patch"],
    "additionalProperties": False,
}