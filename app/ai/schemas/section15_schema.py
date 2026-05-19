SECTION15_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["health_information"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "15A", "15B"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "15A": {
                    "type": "object",
                    "properties": {
                        "primary_health_insurance": {"type": ["string", "null"]},
                        "secondary_health_insurance": {"type": ["string", "null"]},
                        "medicare_medicaid": {"type": ["string", "null"]},
                        "current_conditions": {"type": ["string", "null"]},
                        "allergies": {"type": ["string", "null"]},
                        "current_medications": {"type": ["string", "null"]},
                        "medical_devices": {"type": ["string", "null"]},
                        "emergency_contact_1": {"type": ["string", "null"]},
                        "emergency_contact_2": {"type": ["string", "null"]},
                        "medical_power_of_attorney": {"type": ["string", "null"]},
                    },
                    "additionalProperties": False,
                },
                "15B": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "provider_name": {"type": ["string", "null"]},
                            "specialty": {"type": ["string", "null"]},
                            "doctor_name": {"type": ["string", "null"]},
                            "contact_info": {"type": ["string", "null"]},
                            "patient_id": {"type": ["string", "null"]},
                            "frequency": {"type": ["string", "null"]},
                            "last_visit": {"type": ["string", "null"]},
                            "conditions_treated": {"type": ["string", "null"]},
                            "insurance_accepted": {"type": ["string", "null"]},
                            "portal_access": {"type": ["string", "null"]},
                            "important_notes": {"type": ["string", "null"]},
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