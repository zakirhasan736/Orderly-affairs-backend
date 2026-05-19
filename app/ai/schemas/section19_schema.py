SECTION19_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["assets_valuables"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "19A", "19B"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "19A": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_type": {"type": ["string", "null"]},
                            "item_type_other": {"type": ["string", "null"]},
                            "item_description": {"type": ["string", "null"]},
                            "estimated_value": {"type": ["string", "null"]},
                            "purchase_info": {"type": ["string", "null"]},
                            "current_location": {"type": ["string", "null"]},
                            "insurance_info": {"type": ["string", "null"]},
                            "appraisal_info": {"type": ["string", "null"]},
                            "intended_recipient": {"type": ["string", "null"]},
                            "care_instructions": {"type": ["string", "null"]},
                            "item_history": {"type": ["string", "null"]},
                            "item_documents": {"type": ["string", "null"]},
                        },
                        "additionalProperties": False,
                    },
                },
                "19B": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "property_type": {"type": ["string", "null"]},
                            "property_type_other": {"type": ["string", "null"]},
                            "property_address": {"type": ["string", "null"]},
                            "property_description": {"type": ["string", "null"]},
                            "ownership_details": {"type": ["string", "null"]},
                            "purchase_info": {"type": ["string", "null"]},
                            "current_value": {"type": ["string", "null"]},
                            "mortgage_info": {"type": ["string", "null"]},
                            "rental_info": {"type": ["string", "null"]},
                            "property_manager": {"type": ["string", "null"]},
                            "property_taxes": {"type": ["string", "null"]},
                            "insurance_info": {"type": ["string", "null"]},
                            "intended_disposition": {"type": ["string", "null"]},
                            "property_documents": {"type": ["string", "null"]},
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