from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section14_schema import SECTION14_FULL_SCHEMA

VALID_SECTION14_SUBSECTIONS = {
    "14A",
}

SECTION14_PROMPT = """
You are extracting data for the 'Investment Accounts' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important rules:
- The only supported subsection is 14A.
- 14A means Investment Accounts.
- patch["14A"] must always be an array.
- If the uploaded document describes one investment, brokerage, retirement, pension, annuity, stock, bond, or mutual fund account, return exactly one object inside patch["14A"].
- If the uploaded document describes multiple accounts, return one object per account inside patch["14A"].
- Keep keys exactly as required by schema.
- Never invent account numbers, account values, beneficiaries, advisor contacts, employer details, usernames, passwords, distribution instructions, or document locations.
- If account numbers are masked, copy only the masked value exactly as shown. Never try to complete or unmask it.
- For login credentials, passwords, PINs, or security answers: only copy values if they are clearly present in the uploaded document. If the document only says where credentials are stored, copy that storage location/note into login_credentials.
- If a document only says where statements, beneficiary forms, plan documents, or account files are stored, copy that storage location or note into account_documents.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

Account type normalization:
Use one of these values only if clearly supported:
- 401(k)
- 403(b)
- IRA - Traditional
- IRA - Roth
- SEP-IRA
- Pension
- Brokerage Account
- Mutual Fund
- Bonds
- Stocks
- Annuity
- Other

If the account type is clearly present but does not match the list:
- account_type = "Other"
- account_type_other = the actual account type from the document

Field meanings:
- account_type = normalized investment or retirement account type.
- account_type_other = custom account type when account_type is Other.
- financial_institution = company managing the account, such as Fidelity, Vanguard, Schwab, Merrill, TIAA, Empower, Principal, Morgan Stanley, bank, brokerage, pension administrator, or annuity company.
- account_number = account number or masked account number exactly as shown.
- account_value = current value, approximate balance, vested balance, market value, cash value, share value, statement balance, or portfolio value.
- beneficiaries = primary beneficiaries, contingent beneficiaries, beneficiary percentages, TOD/POD beneficiaries, or beneficiary notes.
- advisor_contact = financial advisor, broker, account manager, plan administrator, phone, email, address, firm name, or contact card details.
- employer_connection = employer-sponsored plan details, employer/company name, HR contact, plan sponsor, or old employer connection.
- login_credentials = online account username/password only if clearly shown, or a note/location describing where login credentials are stored.
- distribution_instructions = RMDs, withdrawal instructions, distribution preferences, rollover notes, pension election notes, annuity payout notes, or beneficiary distribution instructions.
- account_documents = statements, beneficiary forms, plan documents, annuity contracts, stock certificates, bond documents, document storage location, or upload notes.

Common source documents:
- brokerage statement
- retirement account statement
- 401(k) statement
- IRA statement
- pension statement
- annuity contract
- mutual fund statement
- stock certificate
- bond document
- beneficiary designation form
- plan document
- advisor letter
- employer retirement plan document
- screenshot/photo containing investment account details
"""


async def extract_section14_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
    field_catalog: list[dict] | None = None,
):
    if subsection and subsection not in VALID_SECTION14_SUBSECTIONS:
        raise ValueError(f"Invalid Section 14 subsection: {subsection}")

    prompt = SECTION14_PROMPT + f"""

Requested section: investment_accounts
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "investment_accounts"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

Required patch shape:
{{
  "14A": [
    {{
      "account_type": null,
      "account_type_other": null,
      "financial_institution": null,
      "account_number": null,
      "account_value": null,
      "beneficiaries": null,
      "advisor_contact": null,
      "employer_connection": null,
      "login_credentials": null,
      "distribution_instructions": null,
      "account_documents": null
    }}
  ]
}}

If no investment account information is found:
{{
  "14A": []
}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        field_catalog=field_catalog,
        response_schema=SECTION14_FULL_SCHEMA,
    )