SECTION5_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["vehicles"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "5A"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "5A": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "year": {"type": ["string", "null"]},
                            "make": {"type": ["string", "null"]},
                            "model": {"type": ["string", "null"]},
                            "color": {"type": ["string", "null"]},
                            "vin": {"type": ["string", "null"]},
                            "license_plate": {"type": ["string", "null"]},
                            "registration_expiry": {"type": ["string", "null"]},
                            "insurance_company": {"type": ["string", "null"]},
                            "insurance_policy": {"type": ["string", "null"]},
                            "financing": {"type": ["string", "null"]},
                            "maintenance_records": {"type": ["string", "null"]},
                            "parking_location": {"type": ["string", "null"]},
                            "spare_keys": {"type": ["string", "null"]},
                            "notes": {"type": ["string", "null"]},
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