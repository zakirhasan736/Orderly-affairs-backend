SECTION16_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["credit_cards_debt"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "16A", "16B"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "16A": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "card_name": {"type": ["string", "null"]},
                            "card_type": {"type": ["string", "null"]},
                            "card_type_other": {"type": ["string", "null"]},
                            "card_number": {"type": ["string", "null"]},
                            "account_number": {"type": ["string", "null"]},
                            "credit_limit": {"type": ["string", "null"]},
                            "current_balance": {"type": ["string", "null"]},
                            "monthly_payment": {"type": ["string", "null"]},
                            "autopay_setup": {"type": ["string", "null"]},
                            "card_benefits": {"type": ["string", "null"]},
                            "customer_service": {"type": ["string", "null"]},
                            "online_account": {"type": ["string", "null"]},
                            "authorized_users": {"type": ["string", "null"]},
                            "card_documents": {"type": ["string", "null"]},
                        },
                        "additionalProperties": False,
                    },
                },
                "16B": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "debt_type": {"type": ["string", "null"]},
                            "debt_type_other": {"type": ["string", "null"]},
                            "creditor_name": {"type": ["string", "null"]},
                            "account_number": {"type": ["string", "null"]},
                            "current_balance": {"type": ["string", "null"]},
                            "monthly_payment": {"type": ["string", "null"]},
                            "payment_due_date": {"type": ["string", "null"]},
                            "interest_rate": {"type": ["string", "null"]},
                            "payment_method": {"type": ["string", "null"]},
                            "cosigners": {"type": ["string", "null"]},
                            "collateral": {"type": ["string", "null"]},
                            "creditor_contact": {"type": ["string", "null"]},
                            "debt_documents": {"type": ["string", "null"]},
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