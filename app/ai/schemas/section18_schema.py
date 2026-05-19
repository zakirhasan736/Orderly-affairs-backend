# app/ai/schemas/section18_schema.py

def nullable_string():
    return {"type": ["string", "null"]}


SECTION18_18A_PROPERTIES = {
    "employment_status": nullable_string(),
    "employer_name": nullable_string(),
    "job_title": nullable_string(),
    "work_address": nullable_string(),
    "work_phone": nullable_string(),
    "supervisor_hr": nullable_string(),
    "employee_id": nullable_string(),
    "start_date": nullable_string(),
    "salary_wage": nullable_string(),
    "benefits": nullable_string(),
    "vacation_sick_time": nullable_string(),
    "work_equipment": nullable_string(),
    "employment_documents": nullable_string(),
}

SECTION18_18B_PROPERTIES = {
    "business_name": nullable_string(),
    "business_type": nullable_string(),
    "business_type_other": nullable_string(),
    "business_address": nullable_string(),
    "business_phone": nullable_string(),
    "tax_id": nullable_string(),
    "business_description": nullable_string(),
    "ownership_percentage": nullable_string(),
    "business_partners": nullable_string(),
    "key_employees": nullable_string(),
    "succession_plan": nullable_string(),
    "business_attorney": nullable_string(),
    "business_accounts": nullable_string(),
    "business_documents": nullable_string(),
}

SECTION18_18C_PROPERTIES = {
    "employer_name": nullable_string(),
    "job_title": nullable_string(),
    "employment_dates": nullable_string(),
    "job_description": nullable_string(),
    "employer_address": nullable_string(),
    "supervisor_contact": nullable_string(),
    "reason_for_leaving": nullable_string(),
    "achievements": nullable_string(),
    "employment_documents": nullable_string(),
}

SECTION18_18D_PROPERTIES = {
    "income_type": nullable_string(),
    "income_type_other": nullable_string(),
    "income_source": nullable_string(),
    "income_amount": nullable_string(),
    "payment_method": nullable_string(),
    "tax_withholding": nullable_string(),
    "income_contact": nullable_string(),
    "income_documents": nullable_string(),
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


SECTION18_FULL_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": ["employment_business"],
        },
        "scope": {
            "type": "string",
            "enum": ["section", "subsection"],
        },
        "subsection": {
            "type": ["string", "null"],
            "enum": [None, "18A", "18B", "18C", "18D"],
        },
        "confidence": {
            "type": "number",
        },
        "patch": {
            "type": "object",
            "properties": {
                "18A": object_schema(SECTION18_18A_PROPERTIES),
                "18B": array_schema(SECTION18_18B_PROPERTIES),
                "18C": array_schema(SECTION18_18C_PROPERTIES),
                "18D": array_schema(SECTION18_18D_PROPERTIES),
            },
            "additionalProperties": False,
        },
    },
    "required": ["section", "scope", "subsection", "confidence", "patch"],
    "additionalProperties": False,
}