# app/ai/extractors/section18_extractor.py

from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section18_schema import SECTION18_FULL_SCHEMA

VALID_SECTION18_SUBSECTIONS = {
    "18A",
    "18B",
    "18C",
    "18D",
}

SECTION18_PROMPT = """
You are extracting data for the 'Employment & Business' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important global rules:
- 18A Current Employment is a single object.
- 18B Business Ownership is an array because the user can have multiple businesses.
- 18C Past Employment is an array because the user can have multiple previous jobs.
- 18D Income Sources is an array because the user can have multiple income sources.
- patch["18A"] must be an object.
- patch["18B"], patch["18C"], and patch["18D"] must always be arrays.
- If a repeatable subsection is requested and the uploaded document describes one item, return exactly one object inside that subsection array.
- If the document clearly describes multiple businesses/jobs/income sources, return multiple objects.
- Keep keys exactly as required by schema.
- Never invent employer names, business names, account numbers, tax IDs, salary numbers, income amounts, contacts, phone numbers, addresses, or dates.
- If a sensitive document is mentioned but the exact value is not visible, only copy the document location/note if clearly stated.
- If a document is uploaded directly, summarize the document reference in the related *_documents field if useful.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

18A Current Employment field meanings:
- employment_status = current employment status.
- employer_name = current employer or company name.
- job_title = current job title or role.
- work_address = workplace address.
- work_phone = work phone number.
- supervisor_hr = supervisor, manager, HR contact, phone, email, address, or uploaded contact note.
- employee_id = employee identification number.
- start_date = job start date.
- salary_wage = salary, wage, hourly rate, annual compensation, bonus, commission, or pay notes.
- benefits = health insurance, retirement, life insurance, disability, pension, PTO, stock options, or other work benefits.
- vacation_sick_time = accrued vacation, sick leave, PTO, paid leave, or leave balances.
- work_equipment = company laptop, phone, car, tools, badge, uniform, keys, devices, or return instructions.
- employment_documents = employee handbook, contract, offer letter, benefits document, pay stub, or document location/upload note.

18A employment_status normalization:
Use one of these values only if clearly supported:
- Employed Full-Time
- Employed Part-Time
- Self-Employed
- Business Owner
- Retired
- Unemployed
- Disabled
- Other

18B Business Ownership field meanings:
- business_name = legal or trade name of business.
- business_type = legal structure.
- business_type_other = custom business structure when business_type is Other.
- business_address = business physical or mailing address.
- business_phone = business phone number.
- tax_id = EIN, tax ID, business ID, registration number, or tax identifier.
- business_description = business activities, services, products, industry, or operating description.
- ownership_percentage = ownership share, equity percentage, partnership percentage, or member interest.
- business_partners = partners, members, shareholders, co-owners, contact information, or ownership notes.
- key_employees = important employees, managers, officers, payroll contacts, or operations contacts.
- succession_plan = business continuation, sale, transfer, buy-sell agreement, closure, succession, or emergency plan.
- business_attorney = attorney, accountant, CPA, advisor, registered agent, contact info, or uploaded business card note.
- business_accounts = business bank accounts, merchant accounts, credit cards, financial accounts, payment processors, or notes.
- business_documents = formation documents, operating agreement, partnership agreement, bylaws, contracts, licenses, permits, tax records, or document location/upload note.

18B business_type normalization:
Use one of these values only if clearly supported:
- Sole Proprietorship
- Partnership
- LLC
- Corporation
- S-Corporation
- Non-Profit
- Other

If the business structure is clearly present but does not match the list:
- business_type = "Other"
- business_type_other = the actual structure from the document.

18C Past Employment field meanings:
- employer_name = previous employer/company/organization.
- job_title = previous job title or position.
- employment_dates = start date, end date, date range, or employment period.
- job_description = role, responsibilities, duties, work summary, or department.
- employer_address = former employer address and contact info.
- supervisor_contact = former supervisor, manager, HR contact, phone, email, or uploaded contact note.
- reason_for_leaving = resignation, retirement, layoff, termination, contract ended, relocation, or reason stated.
- achievements = accomplishments, awards, promotions, projects, contributions, or performance notes.
- employment_documents = employment letters, references, reviews, W-2s, contracts, or document location/upload note.

18D Income Sources field meanings:
- income_type = category of income.
- income_type_other = custom income category when income_type is Other.
- income_source = employer, agency, pension provider, government agency, business, client, investment firm, tenant, or source name.
- income_amount = amount and frequency, such as monthly, yearly, weekly, hourly, distribution amount, benefit amount.
- payment_method = direct deposit, check, ACH, bank transfer, PayPal, cash, account deposit, or payment notes.
- tax_withholding = tax withholding, deductions, W-4, 1099, estimated tax, federal/state withholding, or tax notes.
- income_contact = contact person, company, department, phone, email, website, or uploaded contact note.
- income_documents = pay stubs, 1099s, W-2s, benefit statements, pension statements, rental records, invoices, or document location/upload note.

18D income_type normalization:
Use one of these values only if clearly supported:
- Salary/Wages
- Social Security
- Pension
- Retirement Account Distributions
- Investment Income
- Rental Income
- Business Income
- Freelance/Contract Work
- Disability Benefits
- Alimony
- Other

If the income type is clearly present but does not match the list:
- income_type = "Other"
- income_type_other = the actual income type from the document.

Common source documents:
- employment contract
- offer letter
- employee handbook
- pay stub
- W-2
- 1099
- benefits statement
- pension statement
- Social Security benefit letter
- business formation document
- operating agreement
- partnership agreement
- business license
- tax document
- client contract
- invoice
- income statement
- resume/CV
- performance review
- previous employment letter
"""


async def extract_section18_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
    field_catalog: list[dict] | None = None,
):
    if subsection and subsection not in VALID_SECTION18_SUBSECTIONS:
        raise ValueError(f"Invalid Section 18 subsection: {subsection}")

    prompt = SECTION18_PROMPT + f"""

Requested section: employment_business
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "employment_business"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

If subsection is "18A", only return current employment data inside patch["18A"].
If subsection is "18B", only return business ownership data inside patch["18B"] as an array.
If subsection is "18C", only return past employment data inside patch["18C"] as an array.
If subsection is "18D", only return income source data inside patch["18D"] as an array.
If subsection is FULL_SECTION, return patch["18A"], patch["18B"], patch["18C"], and patch["18D"] if found.

Required 18A patch shape:
{{
  "18A": {{
    "employment_status": null,
    "employer_name": null,
    "job_title": null,
    "work_address": null,
    "work_phone": null,
    "supervisor_hr": null,
    "employee_id": null,
    "start_date": null,
    "salary_wage": null,
    "benefits": null,
    "vacation_sick_time": null,
    "work_equipment": null,
    "employment_documents": null
  }}
}}

Required 18B patch shape:
{{
  "18B": [
    {{
      "business_name": null,
      "business_type": null,
      "business_type_other": null,
      "business_address": null,
      "business_phone": null,
      "tax_id": null,
      "business_description": null,
      "ownership_percentage": null,
      "business_partners": null,
      "key_employees": null,
      "succession_plan": null,
      "business_attorney": null,
      "business_accounts": null,
      "business_documents": null
    }}
  ]
}}

Required 18C patch shape:
{{
  "18C": [
    {{
      "employer_name": null,
      "job_title": null,
      "employment_dates": null,
      "job_description": null,
      "employer_address": null,
      "supervisor_contact": null,
      "reason_for_leaving": null,
      "achievements": null,
      "employment_documents": null
    }}
  ]
}}

Required 18D patch shape:
{{
  "18D": [
    {{
      "income_type": null,
      "income_type_other": null,
      "income_source": null,
      "income_amount": null,
      "payment_method": null,
      "tax_withholding": null,
      "income_contact": null,
      "income_documents": null
    }}
  ]
}}

If no information is found for the requested subsection:
- for 18A return {{"18A": {{}}}}
- for 18B return {{"18B": []}}
- for 18C return {{"18C": []}}
- for 18D return {{"18D": []}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        field_catalog=field_catalog,
        response_schema=SECTION18_FULL_SCHEMA,
    )