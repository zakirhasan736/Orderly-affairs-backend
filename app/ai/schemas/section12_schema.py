SECTION12_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["banking_financial_accounts"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "12A", "12B"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "12A": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "bank_name": {"type": ["string", "null"]},
                            "account_type": {"type": ["string", "null"]},
                            "account_type_other": {"type": ["string", "null"]},
                            "account_number": {"type": ["string", "null"]},
                            "routing_number": {"type": ["string", "null"]},
                            "account_purpose": {"type": ["string", "null"]},
                            "joint_account_holders": {"type": ["string", "null"]},
                            "beneficiaries": {"type": ["string", "null"]},
                            "bank_contact": {"type": ["string", "null"]},
                            "online_banking": {"type": ["string", "null"]},
                            "online_banking_password": {"type": ["string", "null"]},
                            "automatic_payments": {"type": ["string", "null"]},
                            "debit_cards": {"type": ["string", "null"]},
                            "safe_deposit_box": {"type": ["string", "null"]},
                            "account_documents": {"type": ["string", "null"]},
                        },
                        "additionalProperties": False,
                    },
                },
                "12B": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "service_name": {"type": ["string", "null"]},
                            "service_name_other": {"type": ["string", "null"]},
                            "account_email_phone": {"type": ["string", "null"]},
                            "username": {"type": ["string", "null"]},
                            "password": {"type": ["string", "null"]},
                            "linked_accounts": {"type": ["string", "null"]},
                            "account_balance": {"type": ["string", "null"]},
                            "business_personal": {"type": ["string", "null"]},
                            "regular_transactions": {"type": ["string", "null"]},
                            "security_info": {"type": ["string", "null"]},
                            "service_documents": {"type": ["string", "null"]},
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