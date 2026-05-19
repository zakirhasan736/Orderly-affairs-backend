
SECTION9_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["charitable_giving"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "9A"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "9A": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "charity_name": {"type": ["string", "null"]},
                            "cause_type": {"type": ["string", "null"]},
                            "cause_type_other": {"type": ["string", "null"]},
                            "contribution_type": {"type": ["string", "null"]},
                            "contribution_type_other": {"type": ["string", "null"]},
                            "contribution_amount": {"type": ["string", "null"]},
                            "payment_method": {"type": ["string", "null"]},
                            "account_info": {"type": ["string", "null"]},
                            "contact_details": {"type": ["string", "null"]},
                            "special_instructions": {"type": ["string", "null"]},
                            "will_trust_provision": {"type": ["string", "null"]},
                            "tax_documents": {"type": ["string", "null"]},
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