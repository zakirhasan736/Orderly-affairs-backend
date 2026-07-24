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
        "default_subsection": "vital_info",
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
    When the user uploaded inside a specific section, prefer filling that section
    first if the document actually has data for it.

    Do NOT force-match just because additional_sections exist — that caused
    overview probes (vital_information) to swallow vehicle/insurance docs.
    """
    best_key = classification.get("best_section_key")
    additional = list(classification.get("additional_sections") or [])
    additional_keys = {
        item.get("section_key")
        for item in additional
        if isinstance(item, dict) and item.get("section_key")
    }

    # Vehicle <-> insurance: user is in one of the pair — fill that section first,
    # keep the partner in additional_sections.
    if (
        requested_section_key in VEHICLE_INSURANCE_PAIR
        and (
            best_key in VEHICLE_INSURANCE_PAIR
            or bool(VEHICLE_INSURANCE_PAIR & additional_keys)
            or bool(classification.get("matches_requested_section"))
        )
    ):
        partner_key = (
            "insurance_policies"
            if requested_section_key == "vehicles"
            else "vehicles"
        )
        classification["matches_requested_section"] = True
        classification["best_section_key"] = requested_section_key

        if partner_key not in additional_keys and partner_key != requested_section_key:
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

    # Only keep the requested section as best when the classifier already matched it.
    if classification.get("matches_requested_section"):
        classification["best_section_key"] = requested_section_key
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
- The user chose to upload in section {requested_section_key}. Set matches_requested_section=true ONLY when this document clearly contains fillable data for that section.
- Pick best_section_key={requested_section_key} when the document has useful fillable data for that section.
- Pick a different best_section_key when the document's PRIMARY content belongs elsewhere and {requested_section_key} has little or no useful data.
- vital_information is ONLY for personal identity / vital records (passport, driver's license ID page, birth certificate, SSN card metadata, personal contact sheet). A name printed on an insurance card, vehicle registration, bank statement, or policy does NOT make the document vital_information.
- Auto insurance cards, declarations pages, and vehicle registrations belong to vehicles and/or insurance_policies — not vital_information.
- Many documents span multiple sections. List EVERY other section with distinct fillable data in additional_sections.
- Prefer over-including a partner section when the document clearly contains that section's facts (e.g. auto card → vehicles AND insurance_policies).
- Do not stop at one section if the same document can responsibly fill more.
- Examples of multi-section documents:
  - Auto insurance card: vehicles + insurance_policies (NOT vital_information)
  - Vehicle registration: vehicles (add insurance_policies only if policy details are present)
  - Pay stub: employment_business + banking_financial_accounts
  - Mortgage statement: main_residence + banking_financial_accounts
  - Health insurance card: health_information + insurance_policies
  - Brokerage statement: investment_accounts + banking_financial_accounts
  - ID / passport / birth certificate: vital_information
- When the user uploaded in section X and X has data, set best_section_key=X and put other sections in additional_sections.
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
        # Keep headroom so additional_sections arrays are not truncated.
        max_output_tokens=2048,
    )

    raw_text = getattr(response, "text", None) or ""
    if not raw_text and getattr(response, "candidates", None):
        try:
            parts = response.candidates[0].content.parts or []
            raw_text = "".join(getattr(part, "text", "") or "" for part in parts)
        except Exception:
            raw_text = ""

    try:
        parsed = parse_gemini_json(raw_text)
    except RuntimeError:
        # Soft fallback: prefer mismatch routing over wrongly claiming a match
        # for overview probes (especially vital_information).
        print(
            "⚠️ Classification JSON parse failed; "
            f"falling back with matches=false for section={requested_section_key!r}. "
            f"raw_preview={raw_text[:400]!r}"
        )
        parsed = {
            "best_section_key": requested_section_key,
            "confidence": "low",
            "document_summary": "Could not fully classify this document.",
            "matches_requested_section": False,
            "additional_sections": [],
        }

    best_key = parsed.get("best_section_key")
    if best_key not in SECTION_META_BY_KEY:
        parsed["best_section_key"] = requested_section_key
        # Do not force a match — let routing decide from best_section_key.
        parsed["matches_requested_section"] = bool(
            parsed.get("matches_requested_section")
        )
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
