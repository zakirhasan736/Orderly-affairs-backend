SECTION7_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["insurance_policies"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "7A"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "7A": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "policy_type": {"type": ["string", "null"]},
                            "policy_type_other": {"type": ["string", "null"]},
                            "policy_documents_life": {"type": ["string", "null"]},
                            "policy_company": {"type": ["string", "null"]},
                            "policy_number": {"type": ["string", "null"]},
                            "policy_expiry": {"type": ["string", "null"]},
                            "coverage_amount": {"type": ["string", "null"]},
                            "beneficiaries": {"type": ["string", "null"]},
                            "policy_contact": {"type": ["string", "null"]},
                            "premium_info": {"type": ["string", "null"]},
                            "policy_documents": {"type": ["string", "null"]},
                            "notes": {"type": ["string", "null"]},
                            "member_name": {"type": ["string", "null"]},
                            "member_id": {"type": ["string", "null"]},
                            "group_number": {"type": ["string", "null"]},
                            "plan_name": {"type": ["string", "null"]},
                            "covered_relationship": {"type": ["string", "null"]},
                            "rx_bin": {"type": ["string", "null"]},
                            "rx_pcn": {"type": ["string", "null"]},
                            "rx_grp": {"type": ["string", "null"]},
                            "payer_id": {"type": ["string", "null"]},
                            "pharmacy_benefit_manager": {"type": ["string", "null"]},
                            "benefit_summary": {"type": ["string", "null"]},
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