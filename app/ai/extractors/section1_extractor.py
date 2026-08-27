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
- The BACK of a driver's license / state ID (magnetic stripe, barcodes, CLASS/REST/END, vertical DOB, roadside assistance phone line) is still an identity document — extract DOB and any readable identity fields. Do not treat it as a roadside assistance membership card.
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
- date_of_birth = DOB only (the birth date labeled DOB / Date of Birth). Never use ISS / issue date or EXP / expiration date as DOB — those go in drivers_license_issue_date / drivers_license_expiration_date. Store YYYY-MM-DD internally (September 15, 1978 → 1978-09-15). Do not shift the day. US licenses print MM/DD/YYYY — keep that calendar day.
- social_security_number = last 4 only (e.g. from 923-45-6781 → 6781) or storage note.
- drivers_license_number = driver's license / state ID number (DL # / LIC # / ID #) when printed. Do not use the barcode, magstripe, or PDF417 payload as the DL number.
- drivers_license_dd_number = DD / document discriminator / audit number only (common on Texas licenses, often labeled "DD"). Usually 2–12 characters. Never concatenate barcodes or extra digit strings onto DD.
- drivers_license_class = license class (e.g. C, A, B, M) from CLASS: on the front or back.
- drivers_license_issue_date = issue / ISS date only (not DOB, not EXP). Store YYYY-MM-DD.
- drivers_license_expiration_date = expiration / EXP date only (not DOB, not ISS). Store YYYY-MM-DD.
- phone_number = primary phone.
- phone_password / voicemail_pin / computer_password / email passwords / google_id_password / apple_id_password / frequent_pins / safe_code = "Stored in uploaded document" when shown; never raw secrets.
- primary_email_username / secondary_email_username / google_id_username / apple_id_username = usernames or email addresses.
- safe_location / safe_keys / security_question_answers = locations, key notes, or Q&A text when present.

When the document is a driver's license or state ID (front or back), fill EVERY clearly visible license field above — do not leave DL #, DD, CLASS, ISS, or EXP only in a prose summary.

Example vital_info patch shape when identity fields are visible:
{
  "vital_info": {
    "full_legal_name": "Jordan Michael Casey",
    "date_of_birth": "1979-03-14",
    "social_security_number": "6781",
    "drivers_license_number": "12345678",
    "drivers_license_dd_number": "00",
    "drivers_license_class": "C",
    "drivers_license_issue_date": "2020-11-10",
    "drivers_license_expiration_date": "2030-09-15"
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