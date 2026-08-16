# app/ai/document_classifier.py

import asyncio
import json
import re
from pathlib import Path

from app.ai.extractors.base_extractor import LOCAL_FILE_PREFIX, SUPPORTED_MIME_TYPES
from app.ai.llm_generate import generate_llm_content
from app.ai.json_utils import parse_llm_json
from app.ai.local_document_extract import build_llm_document_contents
from app.ai.document_topic import (
    fill_sections_for_kind,
    format_document_plan_prompt,
    infer_document_kind,
    prefer_inferred_kind,
    skip_sections_for_kind,
)


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
        "document_kind": {
            "type": "string",
            "description": (
                "Specific document type, not a family word. Examples: "
                "auto_insurance_card, health_insurance_card, life_insurance, "
                "homeowners_insurance, vehicle_registration, bank_statement, "
                "drivers_license, other."
            ),
        },
        "document_topic": {
            "type": "string",
            "description": (
                "Subject of this file (e.g. Honda CR-V auto policy, UnitedHealthcare "
                "member card). Never a generic word like 'insurance'."
            ),
        },
        "fill_section_keys": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Only vault sections this document can actually fill.",
        },
        "skip_section_keys": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Sections that share a conceptual word but must not be filled "
                "(auto insurance → skip health_information)."
            ),
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
_HEALTH_DOC_RE = re.compile(
    r"\b("
    r"health\s*insurance|medical\s*insurance|dental\s*insurance|"
    r"medicare|medicaid|rx\s*bin|rxbin|rx\s*pcn|"
    r"member\s*id|group\s*(?:number|#)|payer\s*id|"
    r"united\s*healthcare|blue\s*cross|blue\s*shield|aetna|cigna|"
    r"anthem|humana|kaiser"
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
_BANK_STMT_RE = re.compile(
    r"\b("
    r"bank\s*statement|checking\s*(?:account|statement)|savings\s*(?:account|statement)|"
    r"monthly\s*bank\s*statement|"
    r"routing\s*(?:number|#|no)|aba\s*(?:routing|number)|"
    r"beginning\s*balance|ending\s*balance|"
    r"deposits?\s*(?:and|&)\s*withdrawals?|"
    r"direct\s*deposit|voided\s*check|credit\s*union|national\s*bank"
    r")\b",
    re.I,
)
_PROPERTY_PRIMARY_DOC_RE = re.compile(
    r"\b("
    r"mortgage|deed|homeowner|homeowners|home\s*owner|home\s*insurance|"
    r"property\s*tax|title\s*(?:report|policy)|hoa\s*(?:dues|statement|invoice)|"
    r"closing\s*disclosure|warranty\s*deed|quitclaim|"
    r"utility\s*bill|electric\s*bill|gas\s*bill|water\s*bill"
    r")\b",
    re.I,
)
_BANK_MISROUTE_SECTIONS = frozenset(
    {
        "main_residence",
        "vital_information",
        "vehicles",
        "family_treasured_connections",
        "assets_valuables",
    }
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
    parts.append(str(classification.get("document_kind") or ""))
    parts.append(str(classification.get("document_topic") or ""))
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


def apply_document_kind_plan(
    classification: dict,
    document_text: str | None = None,
) -> dict:
    """Sol work plan: kind → topic → allowed sections → skip the rest."""
    if not isinstance(classification, dict):
        return classification

    blob = f"{document_text or ''} {_classification_text_blob(classification)}"
    kind = str(classification.get("document_kind") or "").strip()
    inferred = infer_document_kind(
        blob,
        section_key=str(classification.get("best_section_key") or ""),
    )
    kind = prefer_inferred_kind(kind, inferred)
    if inferred == "auto_insurance":
        kind = "auto_insurance"
    elif inferred == "health_insurance" and kind not in {"auto_insurance"}:
        kind = "health_insurance"

    topic = str(classification.get("document_topic") or "").strip()
    if not topic or topic.lower() in {"insurance", "document", "card"}:
        summary = str(classification.get("document_summary") or "").strip()
        topic = summary[:160] if summary else (kind or "unknown")

    fill = list(classification.get("fill_section_keys") or [])
    planned_fill = fill_sections_for_kind(kind)
    if planned_fill:
        fill = [key for key in fill if key in planned_fill] or list(planned_fill)
        best = classification.get("best_section_key")
        if best and best in planned_fill and best not in fill:
            fill.insert(0, best)

    skip = list(dict.fromkeys(
        list(classification.get("skip_section_keys") or [])
        + list(skip_sections_for_kind(kind))
    ))
    fill = [key for key in fill if key not in skip]
    skip = [key for key in skip if key not in fill]

    classification["document_kind"] = kind
    classification["document_topic"] = topic
    classification["fill_section_keys"] = fill
    classification["skip_section_keys"] = skip
    classification["work_plan"] = [
        {"role": "ocr", "task": "read_text", "status": "done"},
        {"role": "terra", "task": "repair_bad_pages", "status": "done_if_invoked"},
        {"role": "sol", "task": "understand_topic_then_match_section"},
        *[
            {"role": "luna", "task": "extract_section", "section_key": key}
            for key in fill
        ],
        {"role": "gpt4o", "task": "fallback_extract"},
    ]
    classification["document_plan_prompt"] = format_document_plan_prompt(
        kind=kind,
        topic=topic,
        fill_sections=fill,
        skip_sections=skip,
        target_section=str(classification.get("best_section_key") or "") or None,
    )

    additional = [
        item
        for item in (classification.get("additional_sections") or [])
        if isinstance(item, dict)
        and item.get("section_key") not in skip
        and (
            not fill
            or item.get("section_key") in fill
            or item.get("section_key") == classification.get("best_section_key")
        )
    ]
    classification["additional_sections"] = additional

    best = classification.get("best_section_key")
    if best in skip and fill:
        classification["best_section_key"] = fill[0]
        classification["matches_requested_section"] = False

    return classification


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
    looks_health = bool(_HEALTH_DOC_RE.search(blob)) and not looks_auto
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

    # Auto / vehicle insurance is NOT Healthcare. Strip health even if Sol
    # conceptually matched "insurance".
    if looks_auto:
        classification["additional_sections"] = [
            item
            for item in (classification.get("additional_sections") or [])
            if not (
                isinstance(item, dict)
                and item.get("section_key") == "health_information"
            )
        ]
        if classification.get("best_section_key") == "health_information":
            classification["best_section_key"] = "insurance_policies"
            classification["matches_requested_section"] = False

    # Health/medical cards may also fill Healthcare — never Vehicles.
    if looks_health and not looks_auto:
        classification["additional_sections"] = [
            item
            for item in (classification.get("additional_sections") or [])
            if not (
                isinstance(item, dict) and item.get("section_key") == "vehicles"
            )
        ]
        if classification.get("best_section_key") == "vehicles":
            classification["best_section_key"] = "insurance_policies"
            classification["matches_requested_section"] = False
        _ensure_additional_section(
            classification,
            section_key="health_information",
            data_summary="Health insurance card details for Healthcare.",
            confidence="high",
        )

    # Drop the best section from additional_sections if it leaked in.
    best = classification.get("best_section_key")
    classification["additional_sections"] = [
        item
        for item in (classification.get("additional_sections") or [])
        if isinstance(item, dict) and item.get("section_key") not in (None, best)
    ]

    return apply_document_kind_plan(
        harden_bank_statement_routing(
            correct_identity_document_summary(
                classification,
                document_text=document_text,
            ),
            document_text=document_text,
        ),
        document_text=document_text,
    )


def harden_bank_statement_routing(
    classification: dict,
    document_text: str | None = None,
) -> dict:
    """
    Bank / checking / savings statements belong in Bank Accounts.

    A mailing or home address printed on the statement is customer contact
    info — it is NOT a Main Residence document (deed, mortgage, tax bill).
    """
    if not isinstance(classification, dict):
        return classification

    blob = f"{document_text or ''} {_classification_text_blob(classification)}"
    looks_bank = bool(_BANK_STMT_RE.search(blob))
    looks_property = bool(_PROPERTY_PRIMARY_DOC_RE.search(blob))
    if not looks_bank:
        return classification

    additional = [
        item
        for item in (classification.get("additional_sections") or [])
        if isinstance(item, dict) and item.get("section_key") in SECTION_META_BY_KEY
    ]
    best = classification.get("best_section_key")

    if looks_property:
        if best == "main_residence":
            _ensure_additional_section(
                classification,
                section_key="banking_financial_accounts",
                data_summary="Bank/account details also appear on this property document.",
            )
        elif best == "banking_financial_accounts":
            _ensure_additional_section(
                classification,
                section_key="main_residence",
                data_summary="Property/mortgage details also appear on this statement.",
                confidence="medium",
            )
        return classification

    if best in _BANK_MISROUTE_SECTIONS or not best:
        classification["best_section_key"] = "banking_financial_accounts"
        classification["confidence"] = classification.get("confidence") or "high"
        if best == "main_residence":
            classification["matches_requested_section"] = False
        additional = [
            item
            for item in additional
            if item.get("section_key") != "main_residence"
        ]
        classification["additional_sections"] = additional

    classification["additional_sections"] = [
        item
        for item in (classification.get("additional_sections") or [])
        if not (
            isinstance(item, dict) and item.get("section_key") == "main_residence"
        )
    ]

    best = classification.get("best_section_key")
    if best != "banking_financial_accounts":
        _ensure_additional_section(
            classification,
            section_key="banking_financial_accounts",
            data_summary="Bank statement / account details found in this document.",
        )
    else:
        classification["additional_sections"] = [
            item
            for item in (classification.get("additional_sections") or [])
            if not (
                isinstance(item, dict)
                and item.get("section_key") == "banking_financial_accounts"
            )
        ]

    return classification


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

WORK ORDER (document text is already prepared — OCR first, Terra only on bad pages):
1) Understand KIND and TOPIC from the prepared text. A shared word is not a match
   (insurance, account, statement, card, address, name).
2) MATCH THE SECTION FIRST. Set best_section_key to the one section whose fields
   this document can fill most completely.
3) fill_section_keys = that section plus only other sections with DISTINCT facts
   actually on this document.
4) skip_section_keys = conceptually related sections that must NOT be filled.
5) Extraction workers (Luna / GPT-4o) run ONLY after this plan.

TOPIC → SECTION (do not mix families):
- Auto / vehicle insurance or VIN / plate card → insurance_policies + vehicles
  (NOT health_information, vital_information, main_residence, banking)
- Health / medical / dental / Medicare card → insurance_policies + health_information
  (NOT vehicles)
- Life / disability / umbrella (no vehicle, no medical card) → insurance_policies only
- Homeowners / renters policy → insurance_policies + main_residence (NOT vehicles)
- Vehicle registration / title → vehicles
- Bank / checking / savings statement → banking_financial_accounts
  (NOT main_residence just because a mailing address is printed)
- Mortgage / deed / property tax → main_residence (optionally banking)
- Pay stub / W-2 → employment_business (direct-deposit routing may also fill banking)
  (NOT insurance, NOT investments)
- Brokerage / IRA / 401k statement → investment_accounts (NOT bank, NOT credit cards)
- Credit-card statement → credit_cards_debt (NOT bank)
- Diploma / transcript → education_accomplishments (NOT employment)
- DD-214 / military discharge → military_service (NOT employment)
- Last will / living trust → estate_planning_final_wishes
- Driver's license / passport / birth certificate → vital_information
  (a name on another document is NOT vital_information)
- Gym / club / HOA membership card → community_memberships
- Donation receipt → charitable_giving

CRITICAL SECTION ROUTING (do not violate):
1) Auto insurance card / auto declarations / vehicle registration / VIN / license plate documents:
   - document_kind = auto_insurance_card (or auto_insurance)
   - best_section_key MUST be "insurance_policies" OR "vehicles"
   - ALWAYS put the other one in additional_sections / fill_section_keys
   - skip_section_keys MUST include health_information and vital_information
   - NEVER use main_residence, vital_information, health_information, or banking for these docs
2) Health / medical / dental / Medicare insurance cards (no VIN / vehicle):
   - document_kind = health_insurance_card
   - best_section_key MUST be "insurance_policies"
   - additional_sections / fill_section_keys MAY include health_information
   - skip_section_keys MUST include vehicles
   - NEVER use vital_information just because a person's name appears
3) Generic insurance (life, homeowners, umbrella, disability, etc. WITHOUT vehicle/VIN/plate AND WITHOUT health-card fields):
   - best_section_key MUST be "insurance_policies"
   - Do NOT add vehicles unless the document clearly lists a vehicle/VIN/plate
   - Do NOT add health_information unless it is a medical/health/dental/Medicare card
   - NEVER use vital_information just because a person's name appears on the policy
4) main_residence is ONLY for property/home documents (deed, mortgage, homeowners/renters policy, property tax, utility bill for the home).
   - A vehicle / auto insurance document is NOT main_residence even if it shows a garaging address.
   - A bank / checking / savings statement is NOT main_residence even if it prints the customer's home or mailing address.
5) vital_information is ONLY for personal identity / vital records (passport, driver's license front OR back, birth certificate, SSN card metadata, personal contact sheet).
   - The BACK of a driver's license / state ID (magnetic stripe, 1D/2D barcodes, CLASS/REST/END, vertical DOB) is still a driver's license — NOT a "roadside assistance card".
   - Many U.S. licenses print a roadside-assistance phone number (e.g. "TEXAS ROADSIDE ASSISTANCE: 1-800-…") on the back. That line is printed help text on the ID, not the document type.
   - A name printed on an insurance card does NOT make the document vital_information.
6) Homeowners/renters insurance: best_section_key="insurance_policies" with additional_sections including main_residence (and NOT vehicles unless a vehicle is also listed).
7) Bank statements, checking/savings statements, voided checks, routing/account sheets:
   - best_section_key MUST be "banking_financial_accounts"
   - NEVER use main_residence just because a mailing/home address appears on the statement
   - That address is customer contact info, not a property document
   - Mortgage statements / deeds / property tax bills ARE main_residence (optionally also banking_financial_accounts)

Rules:
- The user chose to upload in section {requested_section_key}. Set matches_requested_section=true ONLY when this document clearly contains fillable data for that section.
- Pick best_section_key={requested_section_key} when the document has useful fillable data for that section.
- Pick a different best_section_key when the document's PRIMARY content belongs elsewhere and {requested_section_key} has little or no useful data.
- Route to the section whose form fields the document can fill most completely and accurately.
- Many documents span multiple sections. List EVERY other section with distinct fillable data in additional_sections.
- Prefer over-including a partner section when the document clearly contains that section's facts (e.g. auto card → vehicles AND insurance_policies).
- Do not stop at one section if the same document can responsibly fill more.
- Examples of multi-section documents:
  - Auto insurance card: vehicles + insurance_policies (NOT health_information, NOT vital_information, NOT main_residence)
  - Life insurance statement: insurance_policies ONLY (NOT vehicles, NOT health_information, NOT vital_information)
  - Health insurance card: insurance_policies + health_information (NOT vehicles)
  - Vehicle registration: vehicles (add insurance_policies only if policy details are present)
  - Homeowners policy: insurance_policies + main_residence (NOT vehicles)
  - Pay stub: employment_business + banking_financial_accounts
  - Mortgage statement: main_residence + banking_financial_accounts
  - Bank / checking / savings statement: banking_financial_accounts (NOT main_residence, even if a home mailing address appears)
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


def _classify_sync(
    *,
    document_url: str,
    mime_type: str,
    requested_section_key: str,
    prepared_text: str | None = None,
):
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise ValueError("Unsupported file type")

    if not document_url.startswith(LOCAL_FILE_PREFIX):
        raise ValueError("Public document URLs are disabled for privacy.")

    file_path = document_url.replace(LOCAL_FILE_PREFIX, "", 1)
    path = Path(file_path)

    if not path.exists() or not path.is_file():
        raise FileNotFoundError("Document file not found")

    local_extract = None
    ready_text = str(prepared_text or "").strip()
    if ready_text:
        local_extract = {
            "text": ready_text,
            "document_text": ready_text,
            "quality": "good",
            "needs_vision": False,
        }

    contents, _extract_meta = build_llm_document_contents(
        path=path,
        mime_type=mime_type,
        prompt=_build_classification_prompt(requested_section_key),
        local_extract=local_extract,
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
        role="sol",
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
        looks_bank = bool(_BANK_STMT_RE.search(document_text))
        inferred = infer_document_kind(
            document_text, section_key=requested_section_key
        )
        planned = fill_sections_for_kind(inferred)
        if planned:
            fallback_best = planned[0]
        elif looks_auto or looks_insurance:
            fallback_best = "insurance_policies"
        elif looks_bank and not _PROPERTY_PRIMARY_DOC_RE.search(document_text):
            fallback_best = "banking_financial_accounts"
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

    enforced = enforce_upload_section_first(
        parsed,
        requested_section_key,
        document_text=document_text,
    )
    try:
        from app.ai.llm_context import get_llm_settings, set_llm_settings

        usage = getattr(response, "_orderly_usage", None)
        usage_compact = {}
        if isinstance(usage, dict):
            usage_compact = {
                "prompt_tokens": usage.get("prompt_tokens"),
                "candidates_tokens": usage.get("candidates_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "estimated_usd": usage.get("estimated_usd"),
                "model": usage.get("model"),
                "provider": usage.get("provider"),
            }
        ctx = get_llm_settings()
        ctx["last_classify_meta"] = {
            "method": _extract_meta.get("method"),
            "quality": _extract_meta.get("quality"),
            "quality_score": _extract_meta.get("quality_score"),
            "needs_vision": bool(_extract_meta.get("needs_vision")),
            "terra_invoked": bool(_extract_meta.get("terra_invoked")),
            "terra_pages": _extract_meta.get("terra_pages") or [],
            "pipeline_path": _extract_meta.get("pipeline_path") or "ocr_sol",
            "source_method": _extract_meta.get("source_method") or "ocr",
            "read_source": _extract_meta.get("read_source") or "system",
            "document_text": document_text[:50000],
            "teacher_model": usage_compact.get("model"),
            "teacher_provider": usage_compact.get("provider"),
            "usage": usage_compact,
        }
        set_llm_settings(ctx)
    except Exception:
        pass
    return enforced


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
    prepared_text: str | None = None,
):
    return await asyncio.to_thread(
        _classify_sync,
        document_url=document_url,
        mime_type=mime_type,
        requested_section_key=requested_section_key,
        prepared_text=prepared_text,
    )


def get_section_meta(section_key: str):
    return SECTION_META_BY_KEY.get(section_key)
