from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section7_schema import SECTION7_FULL_SCHEMA

VALID_SECTION7_SUBSECTIONS = {
    "7A",
}

SECTION7_PROMPT = """
You are extracting data for the 'Insurance Policies' section of an estate planning app.

Return JSON only.
Do not guess.
Read the uploaded document carefully (including OCR text, headers, tables, stamps, and fine print).
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important rules:
- The only supported subsection is 7A.
- 7A means Insurance Policies.
- patch["7A"] must always be an array.
- If the uploaded document describes one insurance policy, return exactly one object inside patch["7A"].
- If the uploaded document describes multiple insurance policies, return one object per policy inside patch["7A"].
- Do NOT create separate 7A objects for coverage lines, insured drivers, vehicles listed on one policy, premium rows, or agent contact blocks — keep those on the same policy object (coverage_amount, beneficiaries, premium_info, policy_contact, notes).
- Keep keys exactly as required by schema. Never rename fields.
- Map values into the exact schema fields below — do not put company names into notes, or policy numbers into coverage_amount.
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
- Bank/Loan
- Mortgage
- Credit
- Other

If the policy type is not one of these, set:
- policy_type = "Other"
- policy_type_other = the actual policy type from the document

Exact field placement (required):
- policy_type = type/category of insurance policy (use the normalized list above). For auto/vehicle cards use "Vehicle". For medical/health insurance ID cards use "Health" (or "Medical/Dental" when the card is clearly dental-only). Never set Health for an auto/vehicle card just because it says "insurance".
- policy_type_other = custom policy type when policy_type is Other.
- policy_documents_life = notes/location/details for life insurance documents, beneficiary forms, statements, or policy packet (Life policies only).
- policy_company = insurance company/carrier name exactly as shown (e.g. "State Farm", "MetLife", "UnitedHealthcare"). Map labels such as Insurance Carrier, Insurer, Insurance Provider, Insurance Name.
- policy_number = policy number, insurance number, certificate number, or plan number when clearly shown. Copy digits/letters exactly; do not invent. Always fill this when a policy/insurance number appears — never leave it only in notes. Treat labels like "Policy #", "Policy No", "Polcy Numbor", "Insurance Number" as policy_number. For health cards, ALSO copy Member ID into member_id (and still set policy_number = Member ID when no separate policy number exists).
- policy_expiry = policy end / expiration / "valid through" / "coverage ends" / end date of a policy period.
  Examples: "Period: 01/01/2025 to 12/31/2025" → policy_expiry = "2025-12-31"
  "Valid from January 1, 2025 through December 31, 2025" → policy_expiry = "2025-12-31"
  Always take the END / TO date of a range, never the start. Prefer YYYY-MM-DD.
  If the document also shows a separate Renewal Date, do not overwrite policy_expiry with it.
- coverage_amount = death benefit, coverage limit, insured amount, benefit value, or liability limit (include currency if shown). Do NOT put deductibles here — use benefit_summary for health-card deductibles/OOP/coinsurance.
- beneficiaries = beneficiary names, percentages, contingent beneficiaries, or beneficiary notes.
- policy_contact = agent, broker, customer service, claims phone, email, address, or business card details.
- premium_info = premium amount, payment schedule, autopay, due date, billing method, payment notes, AND full policy period text when shown.
- policy_documents = policy document notes, card details, statement info, or where the policy documents are stored.
- notes = any other important insurance-policy-related information clearly present that does not fit another field.
  For Vehicle / auto policies that list insured vehicles, ALSO put each vehicle on its own concise line in notes using this exact format so Vehicles section can be filled accurately:
  "Vehicle: YYYY Make Model; VIN: <vin>; Plate: <plate>"
  One line per distinct vehicle. Always include VIN when the declarations page or card shows it (look in vehicle schedule tables for VIN / Veh ID / Identification No.). Copy shared policy_company / policy_number only in their dedicated fields (not duplicated per vehicle line).

Health / medical / dental insurance CARD fields (fill whenever shown — never dump these only into notes):
- member_name = member / subscriber / named insured / policy holder name printed on the card (e.g. "Sebastian Shahvandi"). Do not put the agent or beneficiary here.
- member_id = Member ID / Member # exactly as printed.
- group_number = Group Number / Group # / GRP.
- plan_name = plan product name (e.g. "UnitedHealthcare Choice Plus", "LEVEL FUNDED").
- covered_relationship = leave null unless the card explicitly says spouse/dependent/subscriber relationship.
- rx_bin = RxBIN / BIN for pharmacy.
- rx_pcn = RxPCN / PCN.
- rx_grp = RxGRP / RxGroup / pharmacy group.
- payer_id = Payer ID when shown.
- pharmacy_benefit_manager = pharmacy benefit manager / Rx logo or name (e.g. "Optum Rx").
- benefit_summary = concise multi-line summary of cost-share when shown, for example:
  "INN Ded: $6500 / $13000; OON Ded: $10000 / $20000; INN OOPM: $7500 / $15000; OON OOPM: $20000 / $40000; Coinsurance Office/Spec/ER/UrgCare: 30%"
  Prefer structured lines over a single run-on sentence. Do not invent amounts.

Reading tips:
- Prefer declarations pages, ID cards, and statement headers for company + policy number.
- Prefer schedule/benefit pages for coverage_amount and beneficiaries.
- Prefer billing pages for premium_info and policy period dates.
- Prefer agent/broker blocks for policy_contact.
- Auto insurance cards usually contain BOTH vehicle identity and policy number — always extract policy_company, policy_number, and policy_expiry for this section.
- Health insurance cards usually contain member_name, member_id, group_number, plan_name, RxBIN/RxPCN/RxGRP, payer_id, deductibles, and coinsurance — always fill the dedicated health card fields above.

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
    field_catalog: list[dict] | None = None,
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
      "policy_expiry": null,
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
        field_catalog=field_catalog,
        response_schema=SECTION7_FULL_SCHEMA,
    )