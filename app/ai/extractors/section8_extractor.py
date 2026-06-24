from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section8_schema import SECTION8_FULL_SCHEMA

VALID_SECTION8_SUBSECTIONS = {
    "8A",
}

SECTION8_PROMPT = """
You are extracting data for the 'Community Membership / Group Memberships' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important rules:
- The only supported subsection is 8A.
- 8A means Group Memberships.
- patch["8A"] must always be an array.
- If the uploaded document describes one group, organization, club, association, or membership, return exactly one object inside patch["8A"].
- If the uploaded document describes multiple groups, return one object per group inside patch["8A"].
- Keep keys exactly as required by schema.
- Never invent organization names, membership numbers, contact information, roles, notification instructions, or document locations.
- Do not infer religious, political, or sensitive affiliations. Only include them when clearly stated by the document.
- If organization type is unclear, return null.
- If a document only says where membership documents are stored, copy that storage location or note into documents.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

Organization type normalization:
Use one of these values only if clearly supported:
- Religious/Church
- Professional Association
- Social Club
- Volunteer Organization
- Hobby Group
- Sports/Recreation
- Educational
- Political
- Other

If the organization type is clearly present but does not match the list:
- organization_type = "Other"
- organization_type_other = the actual organization type from the document

Field meanings:
- organization_name = name of the group, club, church, association, nonprofit, volunteer organization, team, hobby group, or membership organization.
- organization_type = category of organization using the allowed values above.
- organization_type_other = custom organization type when organization_type is Other.
- membership_details = role, member ID, membership number, responsibilities, dues, renewal date, status, or account/login notes.
- contact_info = phone, email, address, website, leader/contact person, office contact, or business card details.
- importance = why this group is meaningful, memories, personal notes, or sentimental importance when clearly stated.
- notify_instructions = whether to notify the organization, who should be notified, and any special requests after death/incapacity.
- documents = membership cards, certificates, files, records, storage location, or related document notes.

Common source documents:
- membership card
- club/association statement
- church or religious organization record
- nonprofit/volunteer membership document
- professional association card
- sports club membership
- certificate
- email/letter showing membership details
- screenshots/photos containing membership details
"""


async def extract_section8_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
    field_catalog: list[dict] | None = None,
):
    if subsection and subsection not in VALID_SECTION8_SUBSECTIONS:
        raise ValueError(f"Invalid Section 8 subsection: {subsection}")

    prompt = SECTION8_PROMPT + f"""

Requested section: community_memberships
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "community_memberships"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

Required patch shape:
{{
  "8A": [
    {{
      "organization_name": null,
      "organization_type": null,
      "organization_type_other": null,
      "membership_details": null,
      "contact_info": null,
      "importance": null,
      "notify_instructions": null,
      "documents": null
    }}
  ]
}}

If no community membership information is found:
{{
  "8A": []
}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        field_catalog=field_catalog,
        response_schema=SECTION8_FULL_SCHEMA,
    )