from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section9_schema import SECTION9_FULL_SCHEMA

VALID_SECTION9_SUBSECTIONS = {
    "9A",
}

SECTION9_PROMPT = """
You are extracting data for the 'Charitable Giving / Charitable Contributions' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important rules:
- The only supported subsection is 9A.
- 9A means Charitable Contributions.
- patch["9A"] must always be an array.
- If the uploaded document describes one charity, cause, organization, or donation plan, return exactly one object inside patch["9A"].
- If the uploaded document describes multiple charities, causes, organizations, or donation plans, return one object per charity/cause inside patch["9A"].
- Keep keys exactly as required by schema.
- Never invent charity names, donor IDs, account numbers, login information, payment methods, amounts, contact details, will/trust provisions, or tax-document locations.
- Do not infer religious, political, medical, or sensitive giving categories. Only include them when clearly stated by the document.
- If the document only says where donation receipts, tax documents, or donor records are stored, copy that storage location or note into the correct field.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

Cause type normalization:
Use one of these values only if clearly supported:
- Religious
- Educational
- Medical/Health
- Environmental
- Animal Welfare
- Community Services
- Arts & Culture
- International Aid
- Veterans
- Other

If the cause type is clearly present but does not match the list:
- cause_type = "Other"
- cause_type_other = the actual cause type from the document

Contribution type normalization:
Use one of these values only if clearly supported:
- Regular Ongoing Donations
- Annual Contribution
- Occasional Giving
- Planned in Will/Trust
- Other

If the contribution type is clearly present but does not match the list:
- contribution_type = "Other"
- contribution_type_other = the actual contribution type from the document

Field meanings:
- charity_name = charity, nonprofit, religious organization, foundation, fund, school, hospital, community cause, or organization name.
- cause_type = category of the charitable cause using the allowed values.
- cause_type_other = custom cause category when cause_type is Other.
- contribution_type = how the person gives or plans to give using the allowed contribution values.
- contribution_type_other = custom contribution type when contribution_type is Other.
- contribution_amount = amount and frequency, such as $50/month, $500/year, one-time gift, percentage, stock gift, or planned gift amount.
- payment_method = automatic withdrawal, credit card, check, bank transfer, online giving, donor-advised fund, payroll deduction, or other payment notes.
- account_info = donor ID, donor account number, giving account, online account notes, login/storage notes, or member/donor reference.
- contact_details = phone, email, mailing address, website, donor services contact, development officer, or charity contact person.
- special_instructions = instructions to continue, pause, modify, cancel, redirect, or notify the charity about donations.
- will_trust_provision = bequest, planned gift, will/trust clause, beneficiary designation, legacy gift, or estate-giving notes.
- tax_documents = donation receipts, acknowledgement letters, tax records, annual giving statements, document location, or upload/storage notes.

Common source documents:
- donation receipt
- annual giving statement
- charity acknowledgement letter
- donor account statement
- will/trust charitable provision
- planned giving document
- donor-advised fund statement
- tax document
- pledge form
- email/letter from charity
- screenshot/photo containing charitable giving details
"""


async def extract_section9_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
):
    if subsection and subsection not in VALID_SECTION9_SUBSECTIONS:
        raise ValueError(f"Invalid Section 9 subsection: {subsection}")

    prompt = SECTION9_PROMPT + f"""

Requested section: charitable_giving
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "charitable_giving"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

Required patch shape:
{{
  "9A": [
    {{
      "charity_name": null,
      "cause_type": null,
      "cause_type_other": null,
      "contribution_type": null,
      "contribution_type_other": null,
      "contribution_amount": null,
      "payment_method": null,
      "account_info": null,
      "contact_details": null,
      "special_instructions": null,
      "will_trust_provision": null,
      "tax_documents": null
    }}
  ]
}}

If no charitable giving information is found:
{{
  "9A": []
}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        response_schema=SECTION9_FULL_SCHEMA,
    )