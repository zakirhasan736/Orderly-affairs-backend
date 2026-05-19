SECTION8_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["community_memberships"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "8A"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "8A": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "organization_name": {"type": ["string", "null"]},
                            "organization_type": {"type": ["string", "null"]},
                            "organization_type_other": {"type": ["string", "null"]},
                            "membership_details": {"type": ["string", "null"]},
                            "contact_info": {"type": ["string", "null"]},
                            "importance": {"type": ["string", "null"]},
                            "notify_instructions": {"type": ["string", "null"]},
                            "documents": {"type": ["string", "null"]},
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