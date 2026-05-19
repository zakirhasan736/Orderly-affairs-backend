SECTION11_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["military_service"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "11A"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "11A": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "branch_of_service": {"type": ["string", "null"]},
                            "branch_of_service_other": {"type": ["string", "null"]},
                            "service_dates": {"type": ["string", "null"]},
                            "rank_achieved": {"type": ["string", "null"]},
                            "military_occupational_specialty": {"type": ["string", "null"]},
                            "deployments": {"type": ["string", "null"]},
                            "combat_service": {"type": ["string", "null"]},
                            "awards_decorations": {"type": ["string", "null"]},
                            "discharge_type": {"type": ["string", "null"]},
                            "va_benefits": {"type": ["string", "null"]},
                            "military_documents": {"type": ["string", "null"]},
                            "burial_preferences": {"type": ["string", "null"]},
                            "veteran_contacts": {"type": ["string", "null"]},
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