SECTION20_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["legal_documents_records"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "20A", "20B", "20C"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "20A": {
                    "type": "object",
                    "properties": {
                        "birth_certificate": {"type": ["string", "null"]},
                        "social_security_card": {"type": ["string", "null"]},
                        "passport": {"type": ["string", "null"]},
                        "drivers_license": {"type": ["string", "null"]},
                        "marriage_certificate": {"type": ["string", "null"]},
                        "divorce_decree": {"type": ["string", "null"]},
                        "name_change_documents": {"type": ["string", "null"]},
                        "naturalization_certificate": {"type": ["string", "null"]},
                        "immigration_documents": {"type": ["string", "null"]},
                        "children_birth_certificates": {"type": ["string", "null"]},
                        "adoption_documents": {"type": ["string", "null"]},
                        "custody_agreements": {"type": ["string", "null"]},
                    },
                    "additionalProperties": False,
                },
                "20B": {
                    "type": "object",
                    "properties": {
                        "current_tax_year": {"type": ["string", "null"]},
                        "previous_tax_years": {"type": ["string", "null"]},
                        "tax_preparer_info": {"type": ["string", "null"]},
                        "tax_software": {"type": ["string", "null"]},
                        "business_tax_documents": {"type": ["string", "null"]},
                        "estimated_tax_payments": {"type": ["string", "null"]},
                        "tax_debt_issues": {"type": ["string", "null"]},
                    },
                    "additionalProperties": False,
                },
                "20C": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "document_type": {"type": ["string", "null"]},
                            "document_description": {"type": ["string", "null"]},
                            "parties_involved": {"type": ["string", "null"]},
                            "important_dates": {"type": ["string", "null"]},
                            "document_location": {"type": ["string", "null"]},
                            "renewal_requirements": {"type": ["string", "null"]},
                            "contact_information": {"type": ["string", "null"]},
                            "document_upload": {"type": ["string", "null"]},
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