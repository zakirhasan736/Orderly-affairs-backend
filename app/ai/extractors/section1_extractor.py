from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section1_schema import SECTION1_FULL_SCHEMA

VALID_SECTION1_SUBSECTIONS = {
    "vital_info",
    "next_of_kin",
    "executor_trustee",
    "additional_contacts",
}

SECTION1_PROMPT = """
You are extracting data for the 'Vital Information & Key Contacts' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important rules:
- If a contact subsection is requested and the uploaded document describes one person/contact, return exactly one object inside that subsection array.
- If subsection is null, extract all relevant data for the full section.
- If subsection is provided, only fill that subsection.
- Keep keys exactly as required by schema.
- Never invent passwords, PINs, SSN values, contact names, phone numbers, emails, or addresses.
- If the document only says where sensitive data is stored, copy that location/note.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

Subsection meanings:
- vital_info = personal identity, device access, emails, safe/lockbox, digital IDs, security answers, PIN notes.
- next_of_kin = family or emergency contacts.
- executor_trustee = executor/trustee contacts and related professionals.
- additional_contacts = other important contacts like attorney, CPA, funeral director, advisor.
"""


async def extract_section1_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
):
    if subsection and subsection not in VALID_SECTION1_SUBSECTIONS:
        raise ValueError(f"Invalid Section 1 subsection: {subsection}")

    prompt = SECTION1_PROMPT + f"""

Requested section: vital_information
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "vital_information"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

If subsection is requested:
- include only that subsection key inside patch.
- other patch keys should be empty or omitted if schema permits.
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        response_schema=SECTION1_FULL_SCHEMA,
    )