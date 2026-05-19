# app/ai/schemas/section17_schema.py

def nullable_string():
    return {"type": ["string", "null"]}


SECTION17_17A_PROPERTIES = {
    "family_tree_overview": nullable_string(),
    "genealogy_research": nullable_string(),
    "ancestral_origins": nullable_string(),
    "family_stories": nullable_string(),
    "genealogy_contacts": nullable_string(),
    "family_records": nullable_string(),
    "dna_testing": nullable_string(),
}

SECTION17_17B_PROPERTIES = {
    "person_name": nullable_string(),
    "relationship": nullable_string(),
    "contact_info": nullable_string(),
    "birthdate": nullable_string(),
    "importance": nullable_string(),
    "notify_instructions": nullable_string(),
    "special_considerations": nullable_string(),
    "photos_mementos": nullable_string(),
}

SECTION17_17C_PROPERTIES = {
    "dependent_name": nullable_string(),
    "relationship": nullable_string(),
    "birthdate": nullable_string(),
    "dependency_type": nullable_string(),
    "support_details": nullable_string(),
    "backup_caregivers": nullable_string(),
    "special_needs": nullable_string(),
    "future_care_plans": nullable_string(),
    "legal_documents": nullable_string(),
    "financial_accounts": nullable_string(),
}

SECTION17_17D_PROPERTIES = {
    "friend_name": nullable_string(),
    "friendship_type": nullable_string(),
    "friendship_type_other": nullable_string(),
    "contact_info": nullable_string(),
    "how_we_met": nullable_string(),
    "friendship_significance": nullable_string(),
    "notify_instructions": nullable_string(),
    "shared_memories": nullable_string(),
    "photos_mementos": nullable_string(),
}

SECTION17_17E_PROPERTIES = {
    "person_name": nullable_string(),
    "relationship_type": nullable_string(),
    "relationship_type_other": nullable_string(),
    "contact_info": nullable_string(),
    "relationship_significance": nullable_string(),
    "notify_instructions": nullable_string(),
    "special_notes": nullable_string(),
    "relationship_documents": nullable_string(),
}

SECTION17_17F_PROPERTIES = {
    "item_name": nullable_string(),
    "item_type": nullable_string(),
    "item_type_other": nullable_string(),
    "sentimental_value": nullable_string(),
    "current_location": nullable_string(),
    "intended_recipient": nullable_string(),
    "care_instructions": nullable_string(),
    "estimated_value": nullable_string(),
    "documentation": nullable_string(),
}

SECTION17_17G_PROPERTIES = {
    "pet_name": nullable_string(),
    "pet_type": nullable_string(),
    "pet_type_other": nullable_string(),
    "breed_age": nullable_string(),
    "veterinarian": nullable_string(),
    "medical_history": nullable_string(),
    "feeding_care": nullable_string(),
    "emergency_contact": nullable_string(),
    "long_term_care": nullable_string(),
    "pet_supplies": nullable_string(),
    "registration_microchip": nullable_string(),
    "veterinary_records": nullable_string(),
}


def object_schema(properties: dict):
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }


def array_schema(properties: dict):
    return {
        "type": "array",
        "items": object_schema(properties),
    }


SECTION17_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["family_treasured_connections"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "17A", "17B", "17C", "17D", "17E", "17F", "17G"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "17A": object_schema(SECTION17_17A_PROPERTIES),
                "17B": array_schema(SECTION17_17B_PROPERTIES),
                "17C": array_schema(SECTION17_17C_PROPERTIES),
                "17D": array_schema(SECTION17_17D_PROPERTIES),
                "17E": array_schema(SECTION17_17E_PROPERTIES),
                "17F": array_schema(SECTION17_17F_PROPERTIES),
                "17G": array_schema(SECTION17_17G_PROPERTIES),
            },
            "additionalProperties": False,
        },
    },
    "required": ["section", "scope", "subsection", "confidence", "patch"],
    "additionalProperties": False,
}