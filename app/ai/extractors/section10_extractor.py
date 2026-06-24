from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section10_schema import SECTION10_FULL_SCHEMA

VALID_SECTION10_SUBSECTIONS = {
    "10A",
}

SECTION10_PROMPT = """
You are extracting data for the 'Education & Accomplishments' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important rules:
- The only supported subsection is 10A.
- 10A means Educational Background.
- patch["10A"] must always be an array.
- If the uploaded document describes one school, degree, diploma, certification, transcript, or education record, return exactly one object inside patch["10A"].
- If the uploaded document describes multiple education records, return one object per education record inside patch["10A"].
- Keep keys exactly as required by schema.
- Never invent institution names, degree names, certifications, graduation years, honors, awards, or document locations.
- If a document only says where diplomas, transcripts, certificates, or records are stored, copy that storage location or note into documents.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

Degree type normalization:
Use one of these values only if clearly supported:
- High School Diploma
- Associate Degree
- Bachelor's Degree
- Master's Degree
- Doctoral Degree
- Professional Certification
- Trade Certification
- Other

If the degree/certification type is clearly present but does not match the list:
- degree_type = "Other"
- degree_type_other = the actual degree, diploma, certificate, license, or credential type from the document

Field meanings:
- institution_name = school, college, university, academy, training center, trade school, certification body, or issuing institution.
- degree_type = normalized degree/certification type from the allowed list.
- degree_type_other = custom degree/certification type when degree_type is Other.
- field_of_study = major, minor, program, concentration, department, training field, or area of study.
- graduation_year = graduation year, completion year, issued year, awarded year, or expected graduation year.
- honors_awards = academic honors, awards, scholarships, distinctions, honors society, dean's list, summa/magna/cum laude, special recognition, or achievements.
- documents = diploma, certificate, transcript, license, training record, document number, credential ID, file location, upload note, or where education documents are stored.

Common source documents:
- diploma
- certificate
- transcript
- degree audit
- professional certification
- trade certification
- license document
- resume or CV
- school record
- award certificate
- screenshot/photo containing education details
"""


async def extract_section10_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
    field_catalog: list[dict] | None = None,
):
    if subsection and subsection not in VALID_SECTION10_SUBSECTIONS:
        raise ValueError(f"Invalid Section 10 subsection: {subsection}")

    prompt = SECTION10_PROMPT + f"""

Requested section: education_accomplishments
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "education_accomplishments"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

Required patch shape:
{{
  "10A": [
    {{
      "institution_name": null,
      "degree_type": null,
      "degree_type_other": null,
      "field_of_study": null,
      "graduation_year": null,
      "honors_awards": null,
      "documents": null
    }}
  ]
}}

If no education information is found:
{{
  "10A": []
}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        field_catalog=field_catalog,
        response_schema=SECTION10_FULL_SCHEMA,
    )