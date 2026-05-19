from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section7_schema import SECTION7_FULL_SCHEMA

VALID_SECTION7_SUBSECTIONS = {
    "7A",
}

SECTION7_PROMPT = """
You are extracting data for the 'Insurance Policies' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important rules:
- The only supported subsection is 7A.
- 7A means Insurance Policies.
- patch["7A"] must always be an array.
- If the uploaded document describes one insurance policy, return exactly one object inside patch["7A"].
- If the uploaded document describes multiple insurance policies, return one object per policy inside patch["7A"].
- Keep keys exactly as required by schema.
- Never invent policy numbers, beneficiaries, coverage amounts, premium amounts, contact details, or insurance company names.
- If a document only says where policy documents are stored, copy that storage location or note into policy_documents.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

Policy type normalization:
Use one of these values if clearly supported:
- Life
- Homeowner/Renter
- Vehicle
- Health
- Medical/Dental
- Medicaid Supplements
- Long Term Care
- Disability
- Job Loss
- Umbrella
- Annuity
- Other

If the policy type is not one of these, set:
- policy_type = "Other"
- policy_type_other = the actual policy type from the document

Field meanings:
- policy_type = type/category of insurance policy.
- policy_type_other = custom policy type when policy_type is Other.
- policy_documents_life = notes/location/details for life insurance documents, beneficiary forms, statements, or policy packet.
- policy_company = insurance company/carrier name.
- policy_number = policy number, member ID, certificate number, or plan number when clearly shown.
- coverage_amount = death benefit, coverage limit, insured amount, benefit value, or liability limit.
- beneficiaries = beneficiary names, percentages, contingent beneficiaries, or beneficiary notes.
- policy_contact = agent, broker, customer service, claims phone, email, address, or business card details.
- premium_info = premium amount, payment schedule, autopay, due date, billing method, or payment notes.
- policy_documents = policy document notes, card details, statement info, or where the policy documents are stored.
- notes = any other important insurance-policy-related information clearly present.

Common source documents:
- life insurance policy
- insurance card
- declarations page
- policy statement
- annuity statement
- long-term care policy
- disability insurance policy
- homeowners/renters policy
- auto insurance policy
- health/dental insurance card
- umbrella policy
- screenshots/photos containing insurance details
"""


async def extract_section7_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
):
    if subsection and subsection not in VALID_SECTION7_SUBSECTIONS:
        raise ValueError(f"Invalid Section 7 subsection: {subsection}")

    prompt = SECTION7_PROMPT + f"""

Requested section: insurance_policies
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "insurance_policies"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

Required patch shape:
{{
  "7A": [
    {{
      "policy_type": null,
      "policy_type_other": null,
      "policy_documents_life": null,
      "policy_company": null,
      "policy_number": null,
      "coverage_amount": null,
      "beneficiaries": null,
      "policy_contact": null,
      "premium_info": null,
      "policy_documents": null,
      "notes": null
    }}
  ]
}}

If no insurance policy information is found:
{{
  "7A": []
}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        response_schema=SECTION7_FULL_SCHEMA,
    )