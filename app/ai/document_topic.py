"""
Same-topic document replace: Jeep insurance in, old Jeep insurance out.

A topic is the document kind (insurance vs registration) plus the subject
(VIN, policy number, or vehicle make/model). Different kinds for the same
Jeep stay separate.
"""

from __future__ import annotations

import re
from typing import Any

from app.ai.ai_document_storage import destroy_ai_document_assets
from app.database import ai_documents_collection

VEHICLE_MAKES = (
    "jeep",
    "honda",
    "toyota",
    "ford",
    "chevrolet",
    "chevy",
    "bmw",
    "mercedes",
    "tesla",
    "hyundai",
    "kia",
    "nissan",
    "subaru",
    "mazda",
    "volkswagen",
    "vw",
    "audi",
    "lexus",
    "ram",
    "gmc",
    "dodge",
    "chrysler",
    "volvo",
    "porsche",
    "jaguar",
    "acura",
    "infiniti",
    "mitsubishi",
    "buick",
    "cadillac",
    "lincoln",
    "mini",
    "fiat",
    "rivian",
    "lucid",
    "polestar",
    "genesis",
    "land rover",
    "range rover",
)

# More specific kinds first. "insurance" is a family, not a destination.
_AUTO_KIND_RE = re.compile(
    r"\b("
    r"auto(?:mobile)?\s*(?:insurance|policy|card)|"
    r"vehicle\s*(?:insurance|policy|card)|"
    r"(?:car|truck|suv|jeep|honda|motorcycle)\s*(?:insurance|policy)|"
    r"vin\b|license\s*plate|garaging|year\s*make\s*model|"
    r"collision\s*(?:coverage|deductible)|comprehensive\s*(?:coverage|deductible)|"
    r"bodily\s*injury|motor\s*vehicle"
    r")\b",
    re.I,
)
_HEALTH_KIND_RE = re.compile(
    r"\b("
    r"health\s*insurance|medical\s*insurance|dental\s*insurance|"
    r"medicare|medicaid|rx\s*bin|rxbin|rx\s*pcn|rxpcn|"
    r"member\s*id|group\s*(?:number|#|no)|payer\s*id|"
    r"united\s*healthcare|blue\s*cross|blue\s*shield|aetna|cigna|"
    r"anthem|humana|kaiser|optum"
    r")\b",
    re.I,
)
_LIFE_KIND_RE = re.compile(
    r"\b(life\s*insurance|term\s*life|whole\s*life|universal\s*life)\b",
    re.I,
)
_HOME_KIND_RE = re.compile(
    r"\b(homeowner|homeowners|home\s*insurance|renters?\s*insurance|dwelling)\b",
    re.I,
)

KIND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("auto_insurance", _AUTO_KIND_RE),
    ("health_insurance", _HEALTH_KIND_RE),
    ("life_insurance", _LIFE_KIND_RE),
    ("home_insurance", _HOME_KIND_RE),
    (
        "paystub",
        re.compile(
            r"\b(pay\s*stub|payslip|earnings\s*statement|"
            r"gross\s*pay|net\s*pay|w-?2|form\s*w-?2)\b",
            re.I,
        ),
    ),
    (
        "diploma",
        re.compile(
            r"\b(diploma|transcript|degree\s*conferred|commencement|"
            r"bachelor|master of|university\s+of|high\s*school\s*diploma)\b",
            re.I,
        ),
    ),
    (
        "military",
        re.compile(
            r"\b(dd-?214|honorable\s*discharge|"
            r"certificate\s*of\s*release\s*or\s*discharge)\b",
            re.I,
        ),
    ),
    (
        "will",
        re.compile(
            r"\b(last\s*will|living\s*trust|revocable\s*trust|"
            r"advance\s*directive|healthcare\s*directive)\b",
            re.I,
        ),
    ),
    (
        "credit_card",
        re.compile(
            r"\b(credit\s*card\s*statement|visa\s*ending|mastercard|"
            r"american\s*express|minimum\s*payment\s*due|credit\s*limit)\b",
            re.I,
        ),
    ),
    (
        "brokerage",
        re.compile(
            r"\b(brokerage|ira\b|401\s*\(?k\)?|roth|portfolio\s*summary|"
            r"fidelity|vanguard|schwab|equities|mutual\s*fund)\b",
            re.I,
        ),
    ),
    (
        "mortgage",
        re.compile(
            r"\b(mortgage\s*statement|deed|property\s*tax|closing\s*disclosure|"
            r"escrow\s*balance|principal\s*and\s*interest)\b",
            re.I,
        ),
    ),
    (
        "insurance",
        re.compile(
            r"\b(insurance|insurer|policy|premium|geico|allstate|progressive|"
            r"state\s*farm|liability|coverage|deductible|underwriter)\b",
            re.I,
        ),
    ),
    (
        "registration",
        re.compile(
            r"\b(registration|reg(?:istration)?\s*card|title\s*(?:certificate)?|"
            r"secretary\s*of\s*state)\b",
            re.I,
        ),
    ),
    (
        "license",
        re.compile(r"\b(driver'?s?\s*licen[cs]e|\bdl\b|learner'?s?\s*permit)\b", re.I),
    ),
    ("passport", re.compile(r"\b(passport)\b", re.I)),
    (
        "membership",
        re.compile(
            r"\b(membership\s*(?:card|id)|member\s*since|gym\s*membership|hoa\s*dues)\b",
            re.I,
        ),
    ),
    (
        "charity",
        re.compile(
            r"\b(donation\s*receipt|tax\s*deductible\s*contribution|charitable)\b",
            re.I,
        ),
    ),
    (
        "bank",
        re.compile(r"\b(bank\s*statement|checking|savings|routing\s*number)\b", re.I),
    ),
    ("health", re.compile(r"\b(health\s*insurance|medicare|medicaid|rxbin)\b", re.I)),
]

# Sections this document kind may fill. Anything else that merely shares a
# word (e.g. "insurance") is skipped.
KIND_FILL_SECTIONS: dict[str, tuple[str, ...]] = {
    "auto_insurance": ("insurance_policies", "vehicles"),
    "health_insurance": ("insurance_policies", "health_information"),
    "health": ("insurance_policies", "health_information"),
    "life_insurance": ("insurance_policies",),
    "home_insurance": ("insurance_policies", "main_residence"),
    "registration": ("vehicles",),
    "bank": ("banking_financial_accounts",),
    "paystub": ("employment_business", "banking_financial_accounts"),
    "diploma": ("education_accomplishments",),
    "military": ("military_service",),
    "will": ("estate_planning_final_wishes",),
    "credit_card": ("credit_cards_debt",),
    "brokerage": ("investment_accounts",),
    "mortgage": ("main_residence", "banking_financial_accounts"),
    "license": ("vital_information",),
    "passport": ("vital_information",),
    "membership": ("community_memberships",),
    "charity": ("charitable_giving",),
}

KIND_SKIP_SECTIONS: dict[str, tuple[str, ...]] = {
    "auto_insurance": (
        "health_information",
        "vital_information",
        "main_residence",
    ),
    "health_insurance": ("vehicles", "vital_information", "main_residence"),
    "health": ("vehicles", "vital_information", "main_residence"),
    "life_insurance": ("vehicles", "health_information", "vital_information"),
    "home_insurance": ("vehicles", "health_information"),
    "registration": ("health_information", "insurance_policies", "vital_information"),
    "bank": (
        "main_residence",
        "vital_information",
        "vehicles",
        "health_information",
        "investment_accounts",
        "insurance_policies",
    ),
    "paystub": (
        "insurance_policies",
        "investment_accounts",
        "health_information",
        "vital_information",
    ),
    "diploma": ("employment_business", "military_service", "vital_information"),
    "military": ("employment_business", "education_accomplishments", "vital_information"),
    "will": ("legal_documents_records", "insurance_policies", "vital_information"),
    "credit_card": ("banking_financial_accounts", "investment_accounts", "main_residence"),
    "brokerage": ("banking_financial_accounts", "credit_cards_debt", "main_residence"),
    "mortgage": ("vehicles", "health_information"),
    "license": ("insurance_policies", "vehicles", "health_information"),
    "passport": ("insurance_policies", "vehicles", "health_information"),
    "membership": ("insurance_policies", "health_information", "banking_financial_accounts"),
    "charity": ("banking_financial_accounts", "insurance_policies"),
}

KIND_COMPATIBLE: dict[str, frozenset[str]] = {
    "auto_insurance": frozenset({"auto_insurance", "insurance"}),
    "insurance": frozenset({"auto_insurance", "insurance", "life_insurance", "home_insurance"}),
    "health_insurance": frozenset({"health_insurance", "health"}),
    "health": frozenset({"health_insurance", "health"}),
    "life_insurance": frozenset({"life_insurance", "insurance"}),
    "home_insurance": frozenset({"home_insurance", "insurance"}),
    "registration": frozenset({"registration"}),
    "bank": frozenset({"bank"}),
    "paystub": frozenset({"paystub"}),
    "diploma": frozenset({"diploma"}),
    "military": frozenset({"military"}),
    "will": frozenset({"will"}),
    "credit_card": frozenset({"credit_card"}),
    "brokerage": frozenset({"brokerage"}),
    "mortgage": frozenset({"mortgage"}),
    "license": frozenset({"license", "passport"}),
    "passport": frozenset({"passport", "license"}),
    "membership": frozenset({"membership"}),
    "charity": frozenset({"charity"}),
}

_GENERIC_KINDS = frozenset({"insurance", "health", "other", "unknown", "document", ""})

SECTION_KIND = {
    "insurance_policies": "insurance",
    "health_information": "health",
    "vehicles": "registration",
    "vital_information": "license",
    "banking_financial_accounts": "bank",
    "employment_business": "paystub",
    "education_accomplishments": "diploma",
    "military_service": "military",
    "estate_planning_final_wishes": "will",
    "credit_cards_debt": "credit_card",
    "investment_accounts": "brokerage",
    "main_residence": "mortgage",
    "community_memberships": "membership",
    "charitable_giving": "charity",
}

VIN_RE = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b", re.I)
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")

IDENTITY_KEYS = {
    "vin",
    "vehicle_vin",
    "vin_number",
    "policy_number",
    "policy_no",
    "make",
    "model",
    "year",
    "year_make_and_model",
    "vehicle",
    "insured_vehicle",
    "license_plate",
    "plate_number",
}


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def normalize_filename_stem(name: str | None) -> str:
    raw = str(name or "").strip().lower()
    stem = re.sub(r"\.[a-z0-9]{1,8}$", "", raw)
    stem = re.sub(r"[\s._-]*\(\d+\)$", "", stem)
    stem = re.sub(r"[\s._-]*(copy|副本)$", "", stem)
    stem = re.sub(r"[\s._-]+$", "", stem)
    return " ".join(stem.replace("_", " ").replace("-", " ").split())


def detect_kind(*texts: str | None, section_key: str | None = None) -> str:
    blob = " ".join(str(part or "") for part in texts)
    blob = blob.replace("_", " ").replace("-", " ")
    # Auto beats generic insurance AND health. A Honda insurance card is not Healthcare.
    auto = bool(_AUTO_KIND_RE.search(blob))
    health = bool(_HEALTH_KIND_RE.search(blob))
    if auto and not health:
        return "auto_insurance"
    if health and not auto:
        return "health_insurance"
    if auto and health:
        return "auto_insurance"
    for kind, pattern in KIND_PATTERNS:
        if pattern.search(blob):
            return kind
    mapped = SECTION_KIND.get(str(section_key or "").strip())
    return mapped or ""


def infer_document_kind(*texts: str | None, section_key: str | None = None) -> str:
    return detect_kind(*texts, section_key=section_key)


def prefer_inferred_kind(sol_kind: str | None, inferred: str | None) -> str:
    """Prefer a specific OCR-inferred kind over a generic family word from Sol."""
    inferred_k = str(inferred or "").strip()
    sol_k = str(sol_kind or "").strip()
    if inferred_k in KIND_FILL_SECTIONS and (
        sol_k in _GENERIC_KINDS or sol_k not in KIND_FILL_SECTIONS
    ):
        return inferred_k
    if inferred_k in KIND_FILL_SECTIONS and sol_k == "insurance" and inferred_k != "insurance":
        return inferred_k
    return inferred_k or sol_k


def fill_sections_for_kind(kind: str) -> tuple[str, ...]:
    return KIND_FILL_SECTIONS.get(str(kind or "").strip(), ())


def skip_sections_for_kind(kind: str) -> tuple[str, ...]:
    return KIND_SKIP_SECTIONS.get(str(kind or "").strip(), ())


def kinds_compatible(left: str | None, right: str | None) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    allowed = KIND_COMPATIBLE.get(a)
    return bool(allowed and b in allowed)


def format_document_plan_prompt(
    *,
    kind: str,
    topic: str,
    fill_sections: list[str] | tuple[str, ...],
    skip_sections: list[str] | tuple[str, ...],
    target_section: str | None = None,
) -> str:
    fill = ", ".join(fill_sections) or "(none)"
    skip = ", ".join(skip_sections) or "(none)"
    target = str(target_section or "").strip()
    return (
        "SOL DOCUMENT PLAN (follow exactly):\n"
        f"- Document kind: {kind or 'unknown'}\n"
        f"- Topic: {topic or 'unknown'}\n"
        f"- Fill only these vault sections: {fill}\n"
        f"- Do NOT fill these sections: {skip}\n"
        + (
            f"- You are extracting section `{target}` only.\n"
            if target
            else ""
        )
        + "- A shared word is not a match (insurance, account, statement, card, "
        "address, name). Match this document's kind and topic to the section "
        "whose fields it can actually fill.\n"
        "- Auto/vehicle insurance never fills Healthcare. Health/medical cards "
        "never fill Vehicles. Bank statements never fill Main Residence just "
        "because a mailing address is printed. Paystubs are Employment, not Insurance.\n"
    )


def _find_make(text: str) -> str:
    blob = f" {text.lower()} "
    for make in sorted(VEHICLE_MAKES, key=len, reverse=True):
        if f" {make} " in blob or make.replace(" ", "") in _norm(text):
            return _norm(make)
    return ""


def _find_model(text: str, make: str) -> str:
    if not make:
        return ""
    blob = re.sub(r"[^a-z0-9]+", " ", text.lower())
    tokens = blob.split()
    make_tokens = make.split() if " " in make else [make]
    for index, token in enumerate(tokens):
        if _norm(token) != make_tokens[-1]:
            continue
        nxt = tokens[index + 1] if index + 1 < len(tokens) else ""
        if nxt and nxt not in {"insurance", "policy", "registration", "card", "sample"}:
            if not YEAR_RE.fullmatch(nxt) and len(nxt) >= 2:
                return _norm(nxt)
    return ""


def _walk_identity(value: Any, out: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            key_n = _norm(key)
            if key_n in IDENTITY_KEYS or key_n.endswith("vin") or key_n.endswith("make"):
                text = str(nested or "").strip()
                if text and key_n not in out:
                    out[key_n] = text
            _walk_identity(nested, out)
        return
    if isinstance(value, list):
        for item in value:
            _walk_identity(item, out)


def fingerprint_from_parts(
    *,
    filename: str | None = None,
    summary: str | None = None,
    section_key: str | None = None,
    extra_text: str | None = None,
    fields: dict[str, Any] | None = None,
) -> dict[str, str]:
    blob = " ".join(
        part
        for part in (filename, summary, extra_text, str(section_key or ""))
        if part
    )
    blob = blob.replace("_", " ").replace("-", " ")
    identity: dict[str, str] = {}
    if fields:
        _walk_identity(fields, identity)

    vin = _norm(identity.get("vin") or identity.get("vehicle_vin") or identity.get("vin_number"))
    if not vin:
        match = VIN_RE.search(blob)
        vin = _norm(match.group(1)) if match else ""

    policy = _norm(
        identity.get("policy_number") or identity.get("policy_no") or ""
    )
    ymm = str(identity.get("year_make_and_model") or identity.get("vehicle") or "")
    make = _norm(identity.get("make") or "") or _find_make(f"{blob} {ymm}")
    model = _norm(identity.get("model") or "") or _find_model(f"{blob} {ymm}", make)
    year = _norm(identity.get("year") or "")
    if not year:
        year_match = YEAR_RE.search(ymm or blob)
        year = year_match.group(1) if year_match else ""

    kind = detect_kind(filename, summary, extra_text, section_key=section_key)
    if kind == "registration" and detect_kind(filename, summary) == "insurance":
        kind = "insurance"

    return {
        "kind": kind,
        "vin": vin[:17],
        "policy": policy[:32],
        "make": make,
        "model": model,
        "year": year[:4],
        "stem": normalize_filename_stem(filename),
    }


def fingerprint_is_strong(fp: dict[str, str] | None) -> bool:
    if not fp or not fp.get("kind"):
        return False
    return bool(fp.get("vin") or fp.get("policy") or fp.get("make") or fp.get("stem"))


def fingerprints_match(left: dict[str, str] | None, right: dict[str, str] | None) -> bool:
    if not left or not right:
        return False
    if left.get("stem") and left.get("stem") == right.get("stem"):
        return True

    kind_a = left.get("kind") or ""
    kind_b = right.get("kind") or ""
    if kind_a and kind_b and not kinds_compatible(kind_a, kind_b):
        return False
    if not kind_a and not kind_b:
        return False

    vin_a, vin_b = left.get("vin") or "", right.get("vin") or ""
    if vin_a and vin_b:
        return vin_a == vin_b and (
            not kind_a or not kind_b or kinds_compatible(kind_a, kind_b)
        )

    policy_a, policy_b = left.get("policy") or "", right.get("policy") or ""
    if policy_a and policy_b and len(policy_a) >= 4 and len(policy_b) >= 4:
        if policy_a == policy_b:
            return True
        if kind_a == kind_b == "insurance":
            return False

    make_a, make_b = left.get("make") or "", right.get("make") or ""
    if not make_a or not make_b or make_a != make_b:
        return False

    model_a, model_b = left.get("model") or "", right.get("model") or ""
    if model_a and model_b and model_a != model_b:
        return False

    year_a, year_b = left.get("year") or "", right.get("year") or ""
    if year_a and year_b and year_a != year_b:
        return False

    return bool(kind_a and kind_b and kinds_compatible(kind_a, kind_b))


def fingerprint_from_mongo_doc(
    doc: dict[str, Any] | None,
    *,
    classification: dict[str, Any] | None = None,
    extractions: dict[str, Any] | None = None,
    extra_text: str | None = None,
) -> dict[str, str]:
    row = doc or {}
    classed = classification if isinstance(classification, dict) else row.get("last_classification")
    classed = classed if isinstance(classed, dict) else {}
    cached = extractions if isinstance(extractions, dict) else row.get("cached_extractions")
    cached = cached if isinstance(cached, dict) else {}
    stored = row.get("topic_fingerprint")
    computed = fingerprint_from_parts(
        filename=str(row.get("original_filename") or row.get("name") or ""),
        summary=str(classed.get("document_summary") or row.get("document_summary") or ""),
        section_key=str(
            classed.get("best_section_key")
            or row.get("routed_section")
            or row.get("section")
            or ""
        ),
        extra_text=extra_text,
        fields=cached,
    )
    if isinstance(stored, dict) and stored.get("kind"):
        for key in ("vin", "policy", "make", "model", "year", "kind"):
            if not computed.get(key) and stored.get(key):
                computed[key] = str(stored.get(key) or "")
    return computed


async def delete_matching_topic_documents(
    *,
    user_id: str,
    incoming: dict[str, str],
    keep_file_id: str | None = None,
    section: str | None = None,
    content_hash: str | None = None,
) -> list[str]:
    """Delete S3/Cloudinary + Mongo rows that are the same topic as incoming."""
    if not fingerprint_is_strong(incoming) and not str(content_hash or "").strip():
        return []

    keep = str(keep_file_id or "").strip()
    hash_key = str(content_hash or "").strip()
    section_key = str(section or "").strip()
    replaced: list[str] = []

    cursor = ai_documents_collection.find(
        {"user_id": user_id},
        {
            "_id": 1,
            "path": 1,
            "public_id": 1,
            "resource_type": 1,
            "original_filename": 1,
            "section": 1,
            "routed_section": 1,
            "content_hash": 1,
            "s3_key": 1,
            "s3_bucket": 1,
            "storage": 1,
            "last_classification": 1,
            "cached_extractions": 1,
            "document_summary": 1,
            "topic_fingerprint": 1,
        },
    )

    async for doc in cursor:
        file_id = str(doc.get("_id") or "")
        if not file_id or file_id == keep:
            continue

        same_hash = bool(hash_key) and str(doc.get("content_hash") or "") == hash_key
        other = fingerprint_from_mongo_doc(doc)
        same_topic = fingerprints_match(incoming, other)
        if not same_hash and not same_topic:
            continue

        if section_key:
            doc_section = str(doc.get("section") or doc.get("routed_section") or "").strip()
            if doc_section and doc_section not in (section_key, "overview"):
                # Still replace when the semantic topic matches (Jeep insurance
                # uploaded from overview vs section 7).
                if not same_topic:
                    continue

        destroy_ai_document_assets(doc)
        await ai_documents_collection.delete_one({"_id": doc["_id"]})
        replaced.append(file_id)

    return replaced
