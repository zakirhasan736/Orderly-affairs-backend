from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section20_schema import SECTION20_FULL_SCHEMA

VALID_SECTION20_SUBSECTIONS = {
    "20A",
    "20B",
    "20C",
}

SECTION20_PROMPT = """
You are extracting data for the 'Legal Documents & Records' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important global rules:
- 20A means Personal Legal Documents. It is a single object.
- 20B means Tax Documents. It is a single object.
- 20C means Other Important Documents. It is an array.
- patch["20A"] must be an object when personal legal document data is returned.
- patch["20B"] must be an object when tax document data is returned.
- patch["20C"] must always be an array when other important document data is returned.
- If the uploaded document describes one important document for 20C, return exactly one object inside patch["20C"].
- If the uploaded document describes multiple important documents for 20C, return one object per document.
- Keep keys exactly as required by schema.
- Never invent certificate numbers, passport numbers, Social Security numbers, tax amounts, IRS details, names, dates, locations, legal parties, or document storage locations.
- If sensitive numbers are masked, copy only the masked value exactly as shown. Never complete or unmask it.
- If the uploaded file is a document copy, describe the document and note that a copy was uploaded.
- If a document only says where records are stored, copy that storage location or note into the relevant field.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

20A Personal Legal Documents field meanings:
- birth_certificate = birth certificate details, certificate copy note, issuing location, date, document location, or uploaded copy note.
- social_security_card = Social Security card details, masked SSN only if shown, card location, or uploaded copy note.
- passport = passport details, passport number only if shown, expiration date, country, document location, or uploaded copy note.
- drivers_license = driver's license or state ID details, license number only if shown, expiration date, state, document location, or uploaded copy note.
- marriage_certificate = marriage certificate details, spouse name, marriage date/location, certificate location, or uploaded copy note.
- divorce_decree = divorce decree, legal separation document, court/order details, decree date, case note, document location, or uploaded copy note.
- name_change_documents = legal name change documents, court order details, prior/current names, document location, or uploaded copy note.
- naturalization_certificate = naturalization/citizenship certificate details, certificate number only if shown, country, date, document location, or uploaded copy note.
- immigration_documents = green card, visa, immigration status document, work authorization, document location, expiration, or uploaded copy note.
- children_birth_certificates = children birth certificate details, children names, document location, or uploaded copy note.
- adoption_documents = adoption papers, guardianship documents, court records, document location, or uploaded copy note.
- custody_agreements = custody agreement, visitation agreement, court order, document location, or uploaded copy note.

20B Tax Documents field meanings:
- current_tax_year = current year tax returns, W-2, 1099, supporting schedules, tax document location, or uploaded copy note.
- previous_tax_years = previous tax returns, years covered, archived returns, audit records, document location, or uploaded copy note.
- tax_preparer_info = CPA, accountant, tax preparer name, company, phone, email, address, portal, or contact document.
- tax_software = tax software used, login or storage location only if clearly shown, software file location, account notes, or backup location.
- business_tax_documents = business tax returns, partnership/corporate returns, EIN only if shown, business tax files, or document location.
- estimated_tax_payments = quarterly estimated payments, payment dates, amounts, IRS/state payment notes, vouchers, or payment records.
- tax_debt_issues = tax debt, IRS/state notices, payment plans, liens, penalties, correspondence, audit notices, or tax issue notes.

20C Other Important Documents rules:

document_type normalization:
Use one of these values only if clearly supported:
- Contract
- Lease Agreement
- Loan Document
- Insurance Policy
- Professional License
- Academic Diploma
- Award/Certificate
- Legal Settlement
- Court Order
- Power of Attorney
- Other

20C field meanings:
- document_type = normalized document type from the allowed list.
- document_description = what the document is, why it matters, title, purpose, summary, legal effect, or important details.
- parties_involved = names of people, companies, institutions, courts, agencies, attorneys, lenders, landlords, tenants, or organizations involved.
- important_dates = effective date, expiration date, signing date, renewal date, court date, deadline, maturity date, or other key dates.
- document_location = where the original/copy is stored, folder, safe, fireproof bag, attorney office, cloud storage, or uploaded copy note.
- renewal_requirements = renewal steps, expiration rules, maintenance requirements, required payments, filing requirements, or ongoing action.
- contact_information = related attorney, institution, company, agency, landlord, lender, professional contact, phone, email, address, or website.
- document_upload = uploaded copy note, file description, document scan details, or copy storage location.

Common source documents:
- birth certificate
- Social Security card
- passport
- driver's license
- marriage certificate
- divorce decree
- name change court order
- naturalization certificate
- immigration document
- child custody order
- adoption paper
- tax return
- W-2
- 1099
- IRS/state tax notice
- tax preparer contact card
- contract
- lease agreement
- loan document
- professional license
- diploma
- award certificate
- legal settlement
- court order
- power of attorney
- screenshots or photos containing legal/tax records
"""


async def extract_section20_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
):
    if subsection and subsection not in VALID_SECTION20_SUBSECTIONS:
        raise ValueError(f"Invalid Section 20 subsection: {subsection}")

    prompt = SECTION20_PROMPT + f"""

Requested section: legal_documents_records
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "legal_documents_records"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

If subsection is "20A", only return personal legal document data inside patch["20A"].
If subsection is "20B", only return tax document data inside patch["20B"].
If subsection is "20C", only return other important document data inside patch["20C"].
If subsection is FULL_SECTION, return patch["20A"], patch["20B"], and patch["20C"] if found.

Required 20A patch shape:
{{
  "20A": {{
    "birth_certificate": null,
    "social_security_card": null,
    "passport": null,
    "drivers_license": null,
    "marriage_certificate": null,
    "divorce_decree": null,
    "name_change_documents": null,
    "naturalization_certificate": null,
    "immigration_documents": null,
    "children_birth_certificates": null,
    "adoption_documents": null,
    "custody_agreements": null
  }}
}}

Required 20B patch shape:
{{
  "20B": {{
    "current_tax_year": null,
    "previous_tax_years": null,
    "tax_preparer_info": null,
    "tax_software": null,
    "business_tax_documents": null,
    "estimated_tax_payments": null,
    "tax_debt_issues": null
  }}
}}

Required 20C patch shape:
{{
  "20C": [
    {{
      "document_type": null,
      "document_description": null,
      "parties_involved": null,
      "important_dates": null,
      "document_location": null,
      "renewal_requirements": null,
      "contact_information": null,
      "document_upload": null
    }}
  ]
}}

If no information is found for the requested subsection:
- for 20A return {{"20A": {{}}}}
- for 20B return {{"20B": {{}}}}
- for 20C return {{"20C": []}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        response_schema=SECTION20_FULL_SCHEMA,
    )