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
- If the uploaded document describes one military service period, return exactly one object inside patch["11A"].
- If the uploaded document describes multiple service periods, branches, deployments, or records, return one object per service period inside patch["11A"].
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
"""


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

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        field_catalog=field_catalog,
        response_schema=SECTION11_FULL_SCHEMA,
    )