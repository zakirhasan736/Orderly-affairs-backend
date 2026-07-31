from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section1_schema import SECTION1_FULL_SCHEMA

VALID_SECTION1_SUBSECTIONS = {
    "vital_info",
    "next_of_kin",
    "executor_trustee",
    "additional_contacts",
}

# UI / classifier labels → extractor keys
SECTION1_SUBSECTION_ALIASES = {
    "1A": "vital_info",
    "1a": "vital_info",
    "1B": "next_of_kin",
    "1b": "next_of_kin",
    "1C": "additional_contacts",
    "1c": "additional_contacts",
}


def normalize_section1_subsection(subsection: str | None) -> str | None:
    if not subsection:
        return None
    key = subsection.strip()
    return SECTION1_SUBSECTION_ALIASES.get(key, key)

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
- Fill EVERY clearly visible identity field that maps to the schema (name, DOB, phone, email, last-4 SSN, etc.). Do not leave matched values blank.
- Never invent passwords, PINs, full SSNs, contact names, phone numbers, emails, or addresses.
- For password/PIN schema fields: never return the raw secret; use "Stored in uploaded document" when the document shows or references one.
- For social_security_number: use last 4 digits only, or "Stored in uploaded document" — never a full 9-digit SSN.
- If the document only says where sensitive data is stored, copy that location/note into the matching field.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

Subsection meanings:
- vital_info = personal identity, device access, emails, safe/lockbox, digital IDs, security answers, PIN notes.
- next_of_kin = family or emergency contacts.
- executor_trustee = executor/trustee contacts and related professionals.
- additional_contacts = other important contacts like attorney, CPA, funeral director, advisor.

vital_info field meanings (fill when present in the document):
- full_legal_name = legal name, name as it appears on ID/passport/discharge (e.g. "Jordan Michael Casey").
- other_names = aliases, maiden names, aka.
- date_of_birth = DOB; prefer ISO YYYY-MM-DD when possible (e.g. March 14, 1979 → 1979-03-14).
- social_security_number = last 4 only (e.g. from 923-45-6781 → 6781) or storage note.
- phone_number = primary phone.
- phone_password / voicemail_pin / computer_password / email passwords / google_id_password / apple_id_password / frequent_pins / safe_code = "Stored in uploaded document" when shown; never raw secrets.
- primary_email_username / secondary_email_username / google_id_username / apple_id_username = usernames or email addresses.
- safe_location / safe_keys / security_question_answers = locations, key notes, or Q&A text when present.

Example vital_info patch shape when identity fields are visible:
{
  "vital_info": {
    "full_legal_name": "Jordan Michael Casey",
    "date_of_birth": "1979-03-14",
    "social_security_number": "6781"
  }
}
"""


async def extract_section1_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
    field_catalog: list[dict] | None = None,
):
    subsection = normalize_section1_subsection(subsection)

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
        field_catalog=field_catalog,
        response_schema=SECTION1_FULL_SCHEMA,
    )