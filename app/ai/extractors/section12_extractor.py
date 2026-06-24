from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section12_schema import SECTION12_FULL_SCHEMA

VALID_SECTION12_SUBSECTIONS = {
    "12A",
    "12B",
}

SECTION12_PROMPT = """
You are extracting data for the 'Banking & Financial Accounts' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important global rules:
- patch["12A"] must always be an array when bank account data is returned.
- patch["12B"] must always be an array when digital payment data is returned.
- If the uploaded document describes one account, return exactly one object in the correct array.
- If the uploaded document describes multiple accounts, return one object per account.
- Keep keys exactly as required by schema.
- Never invent bank names, account numbers, routing numbers, usernames, passwords, beneficiaries, card details, safe deposit box details, balances, or security information.
- If account numbers are masked, copy only the masked value exactly as shown. Never try to complete or unmask it.
- For passwords, backup codes, PINs, or security answers: only copy values if they are clearly present in the uploaded document. If the document only says where credentials are stored, copy that storage location/note.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

12A means Bank Accounts.

12A account_type normalization:
Use one of these values only if clearly supported:
- Checking
- Savings
- Money Market
- Certificate of Deposit (CD)
- Business Checking
- Business Savings
- Other

If the account type is clearly present but does not match the list:
- account_type = "Other"
- account_type_other = the actual account type from the document

12A field meanings:
- bank_name = bank or financial institution name.
- account_type = normalized bank account type.
- account_type_other = custom account type when account_type is Other.
- account_number = account number or masked account number exactly as shown.
- routing_number = bank routing number exactly as shown.
- account_purpose = what the account is used for, such as household expenses, emergency fund, payroll, savings, business.
- joint_account_holders = joint owners or authorized account holders.
- beneficiaries = payable-on-death beneficiaries, TOD/POD beneficiaries, or named beneficiaries.
- bank_contact = branch, banker, phone, email, address, website, or contact card details.
- online_banking = online banking username or login ID if clearly present.
- online_banking_password = online banking password only if clearly present.
- automatic_payments = automatic debits, bill payments, transfers, subscriptions, deposits, or recurring payments.
- debit_cards = debit card or ATM card info, last 4 digits, card notes, linked card notes, or card document location.
- safe_deposit_box = safe deposit box number, branch, key location, authorized users, or notes.
- account_documents = statements, signature cards, opening documents, storage location, or upload notes.

12B means Digital Payment Services.

12B service_name normalization:
Use one of these values only if clearly supported:
- PayPal
- Venmo
- Cash App
- Zelle
- Apple Pay
- Google Pay
- Samsung Pay
- Stripe
- Square
- Other

If the service is clearly present but does not match the list:
- service_name = "Other"
- service_name_other = the actual service name from the document

12B business_personal normalization:
Use one of these values only if clearly supported:
- Personal
- Business

12B field meanings:
- service_name = digital payment service name.
- service_name_other = custom payment service name when service_name is Other.
- account_email_phone = email address or phone number connected to the account.
- username = username, handle, merchant ID, or account identifier.
- password = password only if clearly present.
- linked_accounts = linked bank accounts, linked cards, payout accounts, funding sources, or storage note.
- account_balance = current or typical balance if clearly shown.
- business_personal = Personal or Business if clearly shown.
- regular_transactions = recurring transfers, payments, customer payments, subscriptions, or common transaction notes.
- security_info = two-factor authentication, security questions, authenticator app, backup codes, recovery email/phone, or credential storage note.
- service_documents = statements, screenshots, transaction records, account records, or storage location.

Common source documents:
- bank statement
- account opening document
- direct deposit form
- voided check
- routing/account info sheet
- debit card note
- safe deposit box document
- online banking credential sheet
- PayPal/Venmo/Cash App/Zelle/Stripe/Square statement
- payment service screenshot
- transaction record
- tax/accounting document showing financial accounts
"""


async def extract_section12_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
    field_catalog: list[dict] | None = None,
):
    if subsection and subsection not in VALID_SECTION12_SUBSECTIONS:
        raise ValueError(f"Invalid Section 12 subsection: {subsection}")

    prompt = SECTION12_PROMPT + f"""

Requested section: banking_financial_accounts
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "banking_financial_accounts"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

If subsection is "12A", only return bank account data inside patch["12A"].
If subsection is "12B", only return digital payment service data inside patch["12B"].
If subsection is FULL_SECTION, return both patch["12A"] and patch["12B"] if found.

Required 12A patch shape:
{{
  "12A": [
    {{
      "bank_name": null,
      "account_type": null,
      "account_type_other": null,
      "account_number": null,
      "routing_number": null,
      "account_purpose": null,
      "joint_account_holders": null,
      "beneficiaries": null,
      "bank_contact": null,
      "online_banking": null,
      "online_banking_password": null,
      "automatic_payments": null,
      "debit_cards": null,
      "safe_deposit_box": null,
      "account_documents": null
    }}
  ]
}}

Required 12B patch shape:
{{
  "12B": [
    {{
      "service_name": null,
      "service_name_other": null,
      "account_email_phone": null,
      "username": null,
      "password": null,
      "linked_accounts": null,
      "account_balance": null,
      "business_personal": null,
      "regular_transactions": null,
      "security_info": null,
      "service_documents": null
    }}
  ]
}}

If no information is found for the requested subsection:
- for 12A return {{"12A": []}}
- for 12B return {{"12B": []}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        field_catalog=field_catalog,
        response_schema=SECTION12_FULL_SCHEMA,
    )