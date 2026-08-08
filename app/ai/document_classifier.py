# app/ai/document_classifier.py

import asyncio
import json
import re
from pathlib import Path

from app.ai.extractors.base_extractor import LOCAL_FILE_PREFIX, SUPPORTED_MIME_TYPES
from app.ai.llm_generate import generate_llm_content
from app.ai.json_utils import parse_llm_json
from app.ai.local_document_extract import build_llm_document_contents


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
            "description": (
                "Owner-facing AI summary of what this upload is (2–5 sentences). "
                "Include document type, key people/institutions, important numbers "
                "or dates, and what it appears useful for. Plain language, no markdown."
            ),
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

# Auto / vehicle docs — require vehicle-specific signals (not generic "insurance card").
_AUTO_DOC_RE = re.compile(
    r"\b("
    r"auto(?:mobile)?\s*(?:insurance|policy|card)?|vehicle|vin\b|license\s*plate|"
    r"number\s*plate|garaging|make\s*(?:and|&)\s*model|year\s*make\s*model|"
    r"collision\s*(?:coverage|deductible)|comprehensive\s*(?:coverage|deductible)|"
    r"bodily\s*injury|property\s*damage\s*liability|"
    r"(?:car|truck|suv|motorcycle)\s*(?:insurance|policy)|"
    r"motor\s*vehicle|registration\s*(?:card|document)"
    r")\b",
    re.I,
)
_INSURANCE_DOC_RE = re.compile(
    r"\b("
    r"insurance|policy\s*(?:#|no|number|id)?|premium|carrier|coverage|"
    r"declarations?\s*page|insured|beneficiary|deductible|underwriter"
    r")\b",
    re.I,
)
_HOME_DOC_RE = re.compile(
    r"\b("
    r"homeowner|homeowners|home\s*owner|home\s*insurance|renters?|"
    r"dwelling|mortgage|deed|property\s*address|hoa|home\s*policy"
    r")\b",
    re.I,
)
_VITAL_ID_DOC_RE = re.compile(
    r"\b("
    r"passport|birth\s*certificate|social\s*security\s*card|ssn\s*card|"
    r"driver'?s?\s*license|driving\s*licen[cs]e|state\s*id\s*card|national\s*id|"
    # Back-of-license cues (magnetic stripe side) — still a driver's license.
    r"class\s*:\s*[a-z0-9]|rest\s*:\s*|end\s*:\s*|organ\s*donor|"
    r"pdf417|magnetic\s*stripe|license\s*number|dl\s*(?:#|no|number)|"
    r"texas\s*roadside\s*assistance|roadside\s*assistance\s*:\s*1-?800"
    r")\b",
    re.I,
)

# Standalone "roadside assistance" phone lines on state ID backs are NOT a separate card type.
_ROADSIDE_ASSIST_CARD_RE = re.compile(
    r"\broadside\s*assistance\s*card\b|\bthis\s+document\s+is\s+a\s+[^.]*roadside\s*assistance\b",
    re.I,
)
_DRIVER_LICENSE_BACK_RE = re.compile(
    r"\b("
    r"class\s*:\s*[a-z0-9]|rest\s*:\s*(?:none|none\b)|end\s*:\s*(?:none|none\b)|"
    r"driver'?s?\s*licen[cs]e|driving\s*licen[cs]e|state\s*(?:id|identification)|"
    r"magnetic\s*stripe|pdf417|organ\s*donor|"
    r"dob\s*:\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r")\b",
    re.I,
)


def _classification_text_blob(classification: dict) -> str:
    parts = [str(classification.get("document_summary") or "")]
    for item in classification.get("additional_sections") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("data_summary") or ""))
            parts.append(str(item.get("section_key") or ""))
    parts.append(str(classification.get("best_section_key") or ""))
    return " ".join(parts)


def correct_identity_document_summary(
    classification: dict,
    document_text: str | None = None,
) -> dict:
    """
    Fix common mislabels on state ID / driver's license backs.

    Texas (and other) license backs print a roadside-assistance phone number.
    Models often call the whole image a "Roadside Assistance card" — rewrite
    that when classic DL-back cues are present.
    """
    if not isinstance(classification, dict):
        return classification

    summary = str(classification.get("document_summary") or "").strip()
    blob = f"{document_text or ''} {summary}"
    looks_dl_back = bool(_DRIVER_LICENSE_BACK_RE.search(blob))
    mislabeled = bool(_ROADSIDE_ASSIST_CARD_RE.search(summary)) or (
        bool(re.search(r"\broadside\s*assistance\b", summary, re.I))
        and looks_dl_back
        and not re.search(
            r"\b(driver'?s?\s*licen[cs]e|driving\s*licen[cs]e|state\s*id)\b",
            summary,
            re.I,
        )
    )

    if not (looks_dl_back and mislabeled):
        return classification

    dob_match = re.search(
        r"\b(?:DOB|date\s*of\s*birth)\s*[:#]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
        blob,
        re.I,
    )
    dob_bit = f" Date of birth shown: {dob_match.group(1)}." if dob_match else ""
    classification["document_summary"] = (
        "This is the back of a state driver's license / photo ID "
        "(magnetic stripe, barcodes, class/restrictions)."
        f"{dob_bit} "
        "The roadside assistance phone number printed on many state IDs is a "
        "help line on the license, not a separate roadside assistance card."
    ).strip()

    # Prefer Vital Information when this was clearly an ID scan.
    best = classification.get("best_section_key")
    if best not in {"vital_information", "legal_documents_records"}:
        classification["best_section_key"] = "vital_information"
        classification["confidence"] = classification.get("confidence") or "high"

    return classification


def _ensure_additional_section(
    classification: dict,
    *,
    section_key: str,
    data_summary: str,
    confidence: str = "high",
) -> None:
    additional = list(classification.get("additional_sections") or [])
    keys = {
        item.get("section_key")
        for item in additional
        if isinstance(item, dict)
    }
    if section_key in keys:
        classification["additional_sections"] = additional
        return
    if classification.get("best_section_key") == section_key:
        return
    additional.append(
        {
            "section_key": section_key,
            "confidence": confidence,
            "data_summary": data_summary,
        }
    )
    classification["additional_sections"] = additional


def harden_vehicle_insurance_routing(
    classification: dict,
    document_text: str | None = None,
) -> dict:
    """
    Correct common misroutes for insurance / vehicle docs.

    - Auto cards → vehicles + insurance_policies (never vital / residence)
    - Generic insurance (life/home/health) → insurance only — NOT vehicles
    - A name on an insurance card is NOT vital_information
    """
    if not isinstance(classification, dict):
        return classification

    blob = f"{document_text or ''} {_classification_text_blob(classification)}"
    looks_auto = bool(_AUTO_DOC_RE.search(blob))
    looks_home = bool(_HOME_DOC_RE.search(blob))
    looks_insurance = bool(_INSURANCE_DOC_RE.search(blob))
    looks_vital_id = bool(_VITAL_ID_DOC_RE.search(blob)) and not looks_insurance

    additional = [
        item
        for item in (classification.get("additional_sections") or [])
        if isinstance(item, dict) and item.get("section_key") in SECTION_META_BY_KEY
    ]
    best = classification.get("best_section_key")

    # Insurance / auto docs must never land on Vital Information just because a name appears.
    if (looks_insurance or looks_auto) and not looks_vital_id:
        additional = [
            item
            for item in additional
            if item.get("section_key") != "vital_information"
        ]
        if best == "vital_information":
            best = "insurance_policies" if looks_insurance or looks_auto else best
            classification["best_section_key"] = best
            classification["confidence"] = classification.get("confidence") or "high"
            classification["matches_requested_section"] = False
            classification["document_summary"] = (
                classification.get("document_summary")
                or "Insurance document (not a vital identity record)."
            )

    # Strip Main Residence from auto-only documents.
    if looks_auto and not looks_home:
        additional = [
            item
            for item in additional
            if item.get("section_key") != "main_residence"
        ]
        if best == "main_residence":
            if re.search(r"\binsurance|policy|carrier|premium\b", blob, re.I):
                best = "insurance_policies"
            else:
                best = "vehicles"
            classification["best_section_key"] = best
            classification["confidence"] = classification.get("confidence") or "high"
            classification["matches_requested_section"] = False

        classification["additional_sections"] = additional
        if best == "vehicles":
            _ensure_additional_section(
                classification,
                section_key="insurance_policies",
                data_summary="Auto insurance policy details found on this vehicle document.",
            )
        elif best == "insurance_policies":
            _ensure_additional_section(
                classification,
                section_key="vehicles",
                data_summary="Vehicle details found on this insurance document.",
            )
        elif best not in VEHICLE_INSURANCE_PAIR:
            classification["best_section_key"] = "insurance_policies"
            classification["matches_requested_section"] = False
            _ensure_additional_section(
                classification,
                section_key="vehicles",
                data_summary="Vehicle details found on this insurance document.",
            )

    # Non-auto insurance: keep insurance, strip vehicles unless auto signals exist.
    best = classification.get("best_section_key")
    if looks_insurance and not looks_auto:
        if best == "vehicles":
            classification["best_section_key"] = "insurance_policies"
            best = "insurance_policies"
            classification["matches_requested_section"] = False
        classification["additional_sections"] = [
            item
            for item in (classification.get("additional_sections") or [])
            if not (
                isinstance(item, dict) and item.get("section_key") == "vehicles"
            )
        ]
        if looks_home:
            _ensure_additional_section(
                classification,
                section_key="main_residence",
                data_summary="Home/property details found on this insurance document.",
                confidence="medium",
            )

    # Auto pair partner only when this really looks like an auto document.
    best = classification.get("best_section_key")
    if looks_auto and best in VEHICLE_INSURANCE_PAIR:
        partner = (
            "insurance_policies" if best == "vehicles" else "vehicles"
        )
        _ensure_additional_section(
            classification,
            section_key=partner,
            data_summary=(
                "Insurance policy details are also in this document."
                if partner == "insurance_policies"
                else "Vehicle details are also in this document."
            ),
        )
        if not looks_home:
            classification["additional_sections"] = [
                item
                for item in (classification.get("additional_sections") or [])
                if not (
                    isinstance(item, dict)
                    and item.get("section_key") == "main_residence"
                )
            ]

    # Drop the best section from additional_sections if it leaked in.
    best = classification.get("best_section_key")
    classification["additional_sections"] = [
        item
        for item in (classification.get("additional_sections") or [])
        if isinstance(item, dict) and item.get("section_key") not in (None, best)
    ]

    return correct_identity_document_summary(
        classification,
        document_text=document_text,
    )


def enforce_upload_section_first(
    classification: dict,
    requested_section_key: str,
    document_text: str | None = None,
) -> dict:
    """
    When the user uploaded inside a specific section, prefer filling that section
    first if the document actually has data for it.

    Do NOT force-match just because additional_sections exist — that caused
    overview probes (vital_information) to swallow vehicle/insurance docs.
    """
    classification = harden_vehicle_insurance_routing(
        classification,
        document_text=document_text,
    )

    best_key = classification.get("best_section_key")
    additional = list(classification.get("additional_sections") or [])
    additional_keys = {
        item.get("section_key")
        for item in additional
        if isinstance(item, dict) and item.get("section_key")
    }
    blob = f"{document_text or ''} {_classification_text_blob(classification)}"
    looks_auto = bool(_AUTO_DOC_RE.search(blob))

    # Vehicle <-> insurance: user is in one of the pair — fill that section first,
    # keep the partner only for auto-looking documents.
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

        # Only pair vehicles when the document clearly has auto/vehicle content.
        if (
            partner_key == "vehicles"
            and not looks_auto
            and requested_section_key == "insurance_policies"
        ):
            classification["additional_sections"] = [
                item
                for item in additional
                if not (
                    isinstance(item, dict) and item.get("section_key") == "vehicles"
                )
            ]
            return classification

        if partner_key not in additional_keys and partner_key != requested_section_key:
            if partner_key != "vehicles" or looks_auto:
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

CRITICAL SECTION ROUTING (do not violate):
1) Auto insurance card / auto declarations / vehicle registration / VIN / license plate documents:
   - best_section_key MUST be "insurance_policies" OR "vehicles"
   - ALWAYS put the other one in additional_sections
   - NEVER use main_residence, vital_information, or banking for these docs
2) Generic insurance (life, health, homeowners, umbrella, disability, etc. WITHOUT vehicle/VIN/plate):
   - best_section_key MUST be "insurance_policies"
   - Do NOT add vehicles unless the document clearly lists a vehicle/VIN/plate
   - NEVER use vital_information just because a person's name appears on the policy
3) main_residence is ONLY for property/home documents (deed, mortgage, homeowners/renters policy, property tax, utility bill for the home).
   - A vehicle / auto insurance document is NOT main_residence even if it shows a garaging address.
4) vital_information is ONLY for personal identity / vital records (passport, driver's license front OR back, birth certificate, SSN card metadata, personal contact sheet).
   - The BACK of a driver's license / state ID (magnetic stripe, 1D/2D barcodes, CLASS/REST/END, vertical DOB) is still a driver's license — NOT a "roadside assistance card".
   - Many U.S. licenses print a roadside-assistance phone number (e.g. "TEXAS ROADSIDE ASSISTANCE: 1-800-…") on the back. That line is printed help text on the ID, not the document type.
   - A name printed on an insurance card does NOT make the document vital_information.
5) Homeowners/renters insurance: best_section_key="insurance_policies" with additional_sections including main_residence (and NOT vehicles unless a vehicle is also listed).

Rules:
- The user chose to upload in section {requested_section_key}. Set matches_requested_section=true ONLY when this document clearly contains fillable data for that section.
- Pick best_section_key={requested_section_key} when the document has useful fillable data for that section.
- Pick a different best_section_key when the document's PRIMARY content belongs elsewhere and {requested_section_key} has little or no useful data.
- Route to the section whose form fields the document can fill most completely and accurately.
- Many documents span multiple sections. List EVERY other section with distinct fillable data in additional_sections.
- Prefer over-including a partner section when the document clearly contains that section's facts (e.g. auto card → vehicles AND insurance_policies).
- Do not stop at one section if the same document can responsibly fill more.
- Examples of multi-section documents:
  - Auto insurance card: vehicles + insurance_policies (NOT vital_information, NOT main_residence)
  - Life / health insurance statement: insurance_policies ONLY (NOT vehicles, NOT vital_information)
  - Vehicle registration: vehicles (add insurance_policies only if policy details are present)
  - Homeowners policy: insurance_policies + main_residence (NOT vehicles)
  - Pay stub: employment_business + banking_financial_accounts
  - Mortgage statement: main_residence + banking_financial_accounts
  - Health insurance card: health_information + insurance_policies
  - Brokerage statement: investment_accounts + banking_financial_accounts
  - ID / passport / birth certificate: vital_information
  - Driver's license BACK (stripe, barcodes, CLASS/REST/END, roadside assistance phone line): vital_information — summarize as the back of a driver's license / state ID, never as a roadside assistance card
- When the user uploaded in section X and X has data, set best_section_key=X and put other sections in additional_sections.
- additional_sections must NOT include {requested_section_key}.
- If the document is clearly only for a different section and has no useful data for {requested_section_key}, set matches_requested_section=false and set best_section_key to that other section.
- document_summary is shown to the owner on a review screen. Write 2–5 clear sentences (about 80–500 characters) that summarize what was uploaded:
  document type (e.g. bank statement, auto insurance card, DD-214, driver's license back), who/what institution it is from, key people named, important account/policy/VIN numbers when clearly shown, date range or expiry when shown, and what the vault can fill from it.
  Do NOT invent facts. Prefer concrete details from the document over vague wording. No markdown, no bullet lists — flowing prose only.
  Never label a driver's license / state ID (front or back) as a "roadside assistance card" just because a roadside assistance phone number is printed on it.
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

    contents, _extract_meta = build_llm_document_contents(
        path=path,
        mime_type=mime_type,
        prompt=_build_classification_prompt(requested_section_key),
    )
    document_text = str(
        _extract_meta.get("document_text")
        or _extract_meta.get("text")
        or ""
    )
    llm_input = str(
        _extract_meta.get("llm_input")
        or _extract_meta.get("gemini_input")
        or "text"
    )

    response = generate_llm_content(
        contents=contents,
        response_mime_type="application/json",
        response_json_schema=CLASSIFICATION_SCHEMA,
        temperature=0,
        max_output_tokens=2048,
        operation="classify",
        llm_input=llm_input,
        file_name=path.name,
    )

    raw_text = getattr(response, "text", None) or ""
    if not raw_text and getattr(response, "candidates", None):
        try:
            parts = response.candidates[0].content.parts or []
            raw_text = "".join(getattr(part, "text", "") or "" for part in parts)
        except Exception:
            raw_text = ""

    parse_failed = False
    try:
        parsed = parse_llm_json(raw_text)
    except RuntimeError:
        # Soft fallback: NEVER claim a match for overview/vital probes on parse failure.
        parse_failed = True
        print(
            "⚠️ Classification JSON parse failed; "
            f"falling back with matches=false for section={requested_section_key!r}. "
            f"raw_preview={raw_text[:400]!r}"
        )
        # Prefer keyword hints from OCR text over pinning to the probe section.
        looks_insurance = bool(_INSURANCE_DOC_RE.search(document_text))
        looks_auto = bool(_AUTO_DOC_RE.search(document_text))
        if looks_auto or looks_insurance:
            fallback_best = "insurance_policies"
        elif requested_section_key == "vital_information":
            fallback_best = "vital_information"
        else:
            fallback_best = requested_section_key
        parsed = {
            "best_section_key": fallback_best,
            "confidence": "low",
            "document_summary": "Could not fully classify this document.",
            "matches_requested_section": False,
            "additional_sections": [],
        }

    best_key = parsed.get("best_section_key")
    if best_key not in SECTION_META_BY_KEY:
        parsed["best_section_key"] = requested_section_key
        parsed["matches_requested_section"] = False
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

    # Do not force matches=true after a failed parse — that pinned insurance docs to Vital.
    if parse_failed:
        parsed["matches_requested_section"] = False
    elif (
        parsed.get("best_section_key") == requested_section_key
        and (
            parsed.get("matches_requested_section")
            or str(parsed.get("confidence") or "").lower() in {"medium", "high"}
        )
    ):
        parsed["matches_requested_section"] = True

    return enforce_upload_section_first(
        parsed,
        requested_section_key,
        document_text=document_text,
    )


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
