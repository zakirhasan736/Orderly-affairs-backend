# app/ai/document_classifier.py

import asyncio
import json
from pathlib import Path

from google.genai import types

from app.ai.extractors.base_extractor import LOCAL_FILE_PREFIX, SUPPORTED_MIME_TYPES
from app.ai.gemini_generate import generate_gemini_content
from app.ai.json_utils import parse_gemini_json


AI_SECTION_OPTIONS = [
    {
        "key": "vital_information",
        "id": "1",
        "label": "Vital Information & Key Contacts",
        "default_subsection": "1A",
    },
    {
        "key": "vehicles",
        "id": "5",
        "label": "Vehicles",
        "default_subsection": "5A",
    },
    {
        "key": "main_residence",
        "id": "6",
        "label": "Main Residence",
        "default_subsection": "6A",
    },
    {
        "key": "insurance_policies",
        "id": "7",
        "label": "Insurance Policies",
        "default_subsection": "7A",
    },
    {
        "key": "community_memberships",
        "id": "8",
        "label": "Organizations & Memberships",
        "default_subsection": "8A",
    },
    {
        "key": "charitable_giving",
        "id": "9",
        "label": "Charitable Contributions",
        "default_subsection": "9A",
    },
    {
        "key": "education_accomplishments",
        "id": "10",
        "label": "Education History",
        "default_subsection": "10A",
    },
    {
        "key": "military_service",
        "id": "11",
        "label": "Military Service",
        "default_subsection": "11A",
    },
    {
        "key": "banking_financial_accounts",
        "id": "12",
        "label": "Bank Accounts",
        "default_subsection": "12A",
    },
    {
        "key": "passwords_online_accounts",
        "id": "13",
        "label": "Passwords & Online Accounts",
        "default_subsection": "13A",
    },
    {
        "key": "investment_accounts",
        "id": "14",
        "label": "Investments",
        "default_subsection": "14A",
    },
    {
        "key": "health_information",
        "id": "15",
        "label": "Healthcare",
        "default_subsection": "15A",
    },
    {
        "key": "credit_cards_debt",
        "id": "16",
        "label": "Credit Cards & Debt",
        "default_subsection": "16A",
    },
    {
        "key": "family_treasured_connections",
        "id": "17",
        "label": "Family & Relationships",
        "default_subsection": "17A",
    },
    {
        "key": "employment_business",
        "id": "18",
        "label": "Employment & Income",
        "default_subsection": "18A",
    },
    {
        "key": "assets_valuables",
        "id": "19",
        "label": "Assets & Valuables",
        "default_subsection": "19A",
    },
    {
        "key": "legal_documents_records",
        "id": "20",
        "label": "Legal Documents & Records",
        "default_subsection": "20A",
    },
    {
        "key": "estate_planning_final_wishes",
        "id": "21",
        "label": "Estate Planning & Final Wishes",
        "default_subsection": "21A",
    },
]

SECTION_KEY_BY_ID = {item["id"]: item["key"] for item in AI_SECTION_OPTIONS}
SECTION_META_BY_KEY = {item["key"]: item for item in AI_SECTION_OPTIONS}

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "best_section_key": {
            "type": "string",
            "description": "Best matching section key from the allowed list.",
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "document_summary": {
            "type": "string",
            "description": "Short plain-language description of what the document appears to be.",
        },
        "matches_requested_section": {
            "type": "boolean",
            "description": "True if the document contains enough data to autofill the requested section.",
        },
        "additional_sections": {
            "type": "array",
            "description": "Other sections that also contain distinct fillable data in this same document.",
            "items": {
                "type": "object",
                "properties": {
                    "section_key": {
                        "type": "string",
                        "description": "Section key from the allowed list.",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "data_summary": {
                        "type": "string",
                        "description": "Short note about what data for that section is in the document.",
                    },
                },
                "required": ["section_key", "confidence", "data_summary"],
            },
        },
    },
    "required": [
        "best_section_key",
        "confidence",
        "document_summary",
        "matches_requested_section",
        "additional_sections",
    ],
}


VEHICLE_INSURANCE_PAIR = frozenset({"vehicles", "insurance_policies"})


def enforce_upload_section_first(
    classification: dict,
    requested_section_key: str,
) -> dict:
    """
    Multi-section documents always fill the section where the user uploaded first,
    then route to other sections via additional_sections.
    """
    best_key = classification.get("best_section_key")
    additional = list(classification.get("additional_sections") or [])
    additional_keys = {
        item.get("section_key")
        for item in additional
        if isinstance(item, dict) and item.get("section_key")
    }

    if additional_keys:
        classification["matches_requested_section"] = True
        classification["best_section_key"] = requested_section_key
        return classification

    if (
        best_key in VEHICLE_INSURANCE_PAIR
        and requested_section_key in VEHICLE_INSURANCE_PAIR
        and best_key != requested_section_key
    ):
        partner_key = (
            "insurance_policies"
            if requested_section_key == "vehicles"
            else "vehicles"
        )
        classification["matches_requested_section"] = True
        classification["best_section_key"] = requested_section_key

        if partner_key not in additional_keys:
            additional.append(
                {
                    "section_key": partner_key,
                    "confidence": "medium",
                    "data_summary": (
                        "Insurance policy details are also in this document."
                        if partner_key == "insurance_policies"
                        else "Vehicle details are also in this document."
                    ),
                }
            )
            classification["additional_sections"] = additional

        return classification

    if best_key == requested_section_key:
        classification["matches_requested_section"] = True

    return classification


def _build_classification_prompt(requested_section_key: str) -> str:
    options_text = "\n".join(
        f'- {item["key"]}: Section {item["id"]} — {item["label"]}'
        for item in AI_SECTION_OPTIONS
    )

    return f"""
You classify uploaded documents for an estate-planning vault app.

Allowed section keys:
{options_text}

The user is trying to autofill section key: {requested_section_key}

Rules:
- The user chose to upload in section {requested_section_key}. Always set matches_requested_section=true when this document contains data for that section, even if another section also has data.
- Pick best_section_key={requested_section_key} whenever the document has fillable data for the section the user is in.
- Pick a different best_section_key only when the document has no useful data for {requested_section_key}.
- Many documents span multiple sections. List every other section with distinct fillable data in additional_sections.
- Examples of multi-section documents:
  - Auto insurance card: vehicles + insurance_policies
  - Pay stub: employment_business + banking_financial_accounts
  - Mortgage statement: main_residence + banking_financial_accounts
  - Health insurance card: health_information + insurance_policies
  - Brokerage statement: investment_accounts + banking_financial_accounts
  - ID + contact page: vital_information + passwords_online_accounts (only if both have distinct data)
- When the user uploaded in section X, set best_section_key=X if X has data, and put other sections in additional_sections.
- additional_sections must NOT include {requested_section_key}.
- If the document is clearly only for a different section and has no useful data for {requested_section_key}, set matches_requested_section=false and set best_section_key to that other section.
- document_summary must be one short sentence, maximum 120 characters.
- Return JSON only.
"""


def _classify_sync(*, document_url: str, mime_type: str, requested_section_key: str):
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise ValueError("Unsupported file type")

    if not document_url.startswith(LOCAL_FILE_PREFIX):
        raise ValueError("Public document URLs are disabled for privacy.")

    file_path = document_url.replace(LOCAL_FILE_PREFIX, "", 1)
    path = Path(file_path)

    if not path.exists() or not path.is_file():
        raise FileNotFoundError("Document file not found")

    file_bytes = path.read_bytes()

    response = generate_gemini_content(
        contents=[
            types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
            _build_classification_prompt(requested_section_key),
        ],
        response_mime_type="application/json",
        response_json_schema=CLASSIFICATION_SCHEMA,
        temperature=0,
        max_output_tokens=256,
    )

    try:
        parsed = parse_gemini_json(response.text)
    except RuntimeError:
        raise RuntimeError("Gemini returned invalid classification JSON")

    best_key = parsed.get("best_section_key")
    if best_key not in SECTION_META_BY_KEY:
        parsed["best_section_key"] = requested_section_key
        parsed["matches_requested_section"] = True
        parsed["confidence"] = "low"

    if not isinstance(parsed.get("additional_sections"), list):
        parsed["additional_sections"] = []

    parsed["additional_sections"] = [
        item
        for item in parsed["additional_sections"]
        if isinstance(item, dict)
        and item.get("section_key") in SECTION_META_BY_KEY
        and item.get("section_key") != requested_section_key
    ]

    if parsed.get("best_section_key") == requested_section_key:
        parsed["matches_requested_section"] = True

    return enforce_upload_section_first(parsed, requested_section_key)


def build_additional_sections_payload(
    classification: dict,
    requested_section_key: str,
):
    raw = classification.get("additional_sections") or []
    results = []

    for item in raw:
        if not isinstance(item, dict):
            continue

        key = item.get("section_key")
        confidence = str(item.get("confidence") or "low").lower()

        if not key or key == requested_section_key or key not in SECTION_META_BY_KEY:
            continue

        meta = SECTION_META_BY_KEY[key]
        results.append(
            {
                "section_key": key,
                "section_id": meta["id"],
                "section_label": meta["label"],
                "subsection": meta.get("default_subsection"),
                "data_summary": item.get("data_summary") or "",
                "confidence": confidence,
            }
        )

    return results


async def classify_document_for_section(
    *,
    document_url: str,
    mime_type: str,
    requested_section_key: str,
):
    return await asyncio.to_thread(
        _classify_sync,
        document_url=document_url,
        mime_type=mime_type,
        requested_section_key=requested_section_key,
    )


def get_section_meta(section_key: str):
    return SECTION_META_BY_KEY.get(section_key)
