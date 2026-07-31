from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section11_schema import SECTION11_FULL_SCHEMA

VALID_SECTION11_SUBSECTIONS = {
    "11A",
}

SECTION11_PROMPT = """
You are extracting data for the 'Military Service Record' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important rules:
- The only supported subsection is 11A.
- 11A means Military Service Record.
- patch["11A"] must always be an array.
- ONE DOCUMENT / ONE SERVICE RECORD (critical): A DD-214, discharge paper, or single continuous enlistment for one person is ONE service period. Return exactly ONE object in patch["11A"].
- Do NOT create separate 11A objects for duty stations, units, deployments, awards, medals, MOS lines, or individual form boxes on the same discharge. Put those into the matching fields on that single object (especially deployments and awards_decorations).
- Extra 11A objects are allowed ONLY when the document clearly shows distinct enlistments/periods with different service date ranges and/or different branches (e.g. Army 2001–2009 AND Navy 2010–2015).
- Keep keys exactly as required by schema.
- Never invent service dates, ranks, MOS codes, deployments, discharge type, VA benefits, awards, burial preferences, or veteran organization contacts.
- If a document only says where DD-214, service records, discharge papers, or VA documents are stored, copy that storage location or note into military_documents.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

Branch of service normalization:
Use one of these values only if clearly supported:
- Army
- Navy
- Air Force
- Marines
- Coast Guard
- Space Force
- National Guard
- Reserves
- Other

If the branch is clearly present but does not match the list:
- branch_of_service = "Other"
- branch_of_service_other = the actual branch/service name from the document

Combat service:
- Use "Yes" only if the document clearly states combat service, combat zone, hostile fire, combat deployment, or similar.
- Use "No" only if the document clearly states no combat service.
- Otherwise use null.

Discharge type normalization:
Use one of these values only if clearly supported:
- Honorable
- General (Under Honorable Conditions)
- Other Than Honorable
- Bad Conduct
- Dishonorable
- Medical

Field meanings:
- branch_of_service = military branch from the allowed list.
- branch_of_service_other = custom branch/service name when branch_of_service is Other.
- service_dates = start and end dates of military service.
- rank_achieved = highest rank, final rank, pay grade, or rank at discharge.
- military_occupational_specialty = MOS, AFSC, NEC, rating, specialty code, job title, or military occupation.
- deployments = duty stations, deployments, overseas service, campaigns, ships, units, bases, or locations served.
- combat_service = Yes/No if clearly stated.
- awards_decorations = awards, medals, ribbons, commendations, badges, citations, or decorations.
- discharge_type = discharge characterization/type.
- va_benefits = VA benefits, disability rating, pension, healthcare, compensation, education benefits, or current VA services.
- military_documents = DD-214, discharge papers, service records, VA letters, document numbers, file locations, upload notes, or where documents are stored.
- burial_preferences = military funeral honors, national cemetery preference, flag preference, veteran burial benefits, or related instructions.
- veteran_contacts = VFW, American Legion, VA office, veteran service officer, veterans organization, phone, email, address, or contact notes.

Common source documents:
- DD-214
- discharge papers
- military service record
- VA benefits letter
- VA disability rating letter
- award certificate
- military ID or veteran ID
- burial benefit document
- national cemetery document
- VFW or American Legion membership/contact record
- resume/CV showing service
- screenshot/photo containing military service details

DD-214 / discharge mapping examples (one object only):
- Name / SSN on a discharge do NOT create extra 11A cards — leave identity-only facts out unless they fit military_documents notes.
- Department/Component (e.g. Army, Regular Army) → branch_of_service (and branch_of_service_other if needed).
- Grade/Rank → rank_achieved (include pay grade when present, e.g. "Staff Sergeant (E-6)").
- Date Entered Active Duty + Separation Date → service_dates (one combined string).
- Last Duty Assignment / units / places of entry → deployments (one combined string).
- MOS → military_occupational_specialty.
- Decorations/Medals/Badges → awards_decorations (one combined string).
- Character of Service → discharge_type.
"""


_SERVICE_PERIOD_MERGE_KEYS = (
    "branch_of_service",
    "branch_of_service_other",
    "service_dates",
    "rank_achieved",
    "military_occupational_specialty",
    "deployments",
    "combat_service",
    "awards_decorations",
    "discharge_type",
    "va_benefits",
    "military_documents",
    "burial_preferences",
    "veteran_contacts",
)


def _norm_military_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _merge_military_text(existing, incoming) -> str | None:
    a = (existing or "").strip() if isinstance(existing, str) else ""
    b = (incoming or "").strip() if isinstance(incoming, str) else ""
    if not a:
        return b or None
    if not b:
        return a or None
    na, nb = _norm_military_text(a), _norm_military_text(b)
    if na == nb:
        return a
    if nb in na:
        return a
    if na in nb:
        return b
    return f"{a}; {b}"


def _merge_service_period_dicts(items: list[dict]) -> dict:
    merged: dict = {key: None for key in _SERVICE_PERIOD_MERGE_KEYS}
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in _SERVICE_PERIOD_MERGE_KEYS:
            incoming = item.get(key)
            if incoming is None or incoming == "":
                continue
            if key in {
                "deployments",
                "awards_decorations",
                "va_benefits",
                "military_documents",
                "burial_preferences",
                "veteran_contacts",
                "military_occupational_specialty",
            }:
                merged[key] = _merge_military_text(merged.get(key), incoming)
            elif merged.get(key) in (None, ""):
                merged[key] = incoming
            else:
                # Prefer the longer concrete string when both set
                current = str(merged[key])
                candidate = str(incoming)
                if len(candidate) > len(current):
                    merged[key] = incoming
    return merged


def collapse_section11_service_periods(items: list) -> list:
    """
    Keep distinct enlistments; collapse DD-214 fragment splits into one card.
    """
    if not isinstance(items, list) or len(items) <= 1:
        return items if isinstance(items, list) else []

    periods = [item for item in items if isinstance(item, dict)]
    if len(periods) <= 1:
        return periods

    date_values = {
        _norm_military_text(item.get("service_dates"))
        for item in periods
        if _norm_military_text(item.get("service_dates"))
    }
    branch_values = {
        _norm_military_text(item.get("branch_of_service"))
        or _norm_military_text(item.get("branch_of_service_other"))
        for item in periods
        if _norm_military_text(item.get("branch_of_service"))
        or _norm_military_text(item.get("branch_of_service_other"))
    }

    # Same branch (or no branch) and not multiple distinct date ranges → one period
    if len(date_values) <= 1 and len(branch_values) <= 1:
        return [_merge_service_period_dicts(periods)]

    # Group by (branch, dates); merge fragments inside each group
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in periods:
        branch = (
            _norm_military_text(item.get("branch_of_service"))
            or _norm_military_text(item.get("branch_of_service_other"))
            or "_"
        )
        dates = _norm_military_text(item.get("service_dates")) or "_"
        groups.setdefault((branch, dates), []).append(item)

    return [_merge_service_period_dicts(group) for group in groups.values()]


async def extract_section11_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
    field_catalog: list[dict] | None = None,
):
    if subsection and subsection not in VALID_SECTION11_SUBSECTIONS:
        raise ValueError(f"Invalid Section 11 subsection: {subsection}")

    prompt = SECTION11_PROMPT + f"""

Requested section: military_service
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "military_service"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

Required patch shape:
{{
  "11A": [
    {{
      "branch_of_service": null,
      "branch_of_service_other": null,
      "service_dates": null,
      "rank_achieved": null,
      "military_occupational_specialty": null,
      "deployments": null,
      "combat_service": null,
      "awards_decorations": null,
      "discharge_type": null,
      "va_benefits": null,
      "military_documents": null,
      "burial_preferences": null,
      "veteran_contacts": null
    }}
  ]
}}

If no military service information is found:
{{
  "11A": []
}}
"""

    result = await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        field_catalog=field_catalog,
        response_schema=SECTION11_FULL_SCHEMA,
    )

    if isinstance(result, dict):
        patch = result.get("patch")
        if isinstance(patch, dict) and isinstance(patch.get("11A"), list):
            patch["11A"] = collapse_section11_service_periods(patch["11A"])
            result["patch"] = patch

    return result