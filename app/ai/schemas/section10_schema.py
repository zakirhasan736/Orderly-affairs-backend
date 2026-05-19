SECTION10_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["education_accomplishments"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "10A"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "10A": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "institution_name": {"type": ["string", "null"]},
                            "degree_type": {"type": ["string", "null"]},
                            "degree_type_other": {"type": ["string", "null"]},
                            "field_of_study": {"type": ["string", "null"]},
                            "graduation_year": {"type": ["string", "null"]},
                            "honors_awards": {"type": ["string", "null"]},
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