SECTION13_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["passwords_online_accounts"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "13A"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "13A": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "account_type": {"type": ["string", "null"]},
                            "account_type_other": {"type": ["string", "null"]},
                            "service_name": {"type": ["string", "null"]},
                            "account_username": {"type": ["string", "null"]},
                            "account_password": {"type": ["string", "null"]},
                            "email_associated": {"type": ["string", "null"]},
                            "phone_associated": {"type": ["string", "null"]},
                            "recovery_info": {"type": ["string", "null"]},
                            "two_factor_auth": {"type": ["string", "null"]},
                            "account_value": {"type": ["string", "null"]},
                            "closure_instructions": {"type": ["string", "null"]},
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