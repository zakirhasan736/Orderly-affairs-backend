from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section16_schema import SECTION16_FULL_SCHEMA

VALID_SECTION16_SUBSECTIONS = {
    "16A",
    "16B",
}

SECTION16_PROMPT = """
You are extracting data for the 'Credit Cards & Debt' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important global rules:
- 16A means Credit Cards.
- 16B means Other Debts.
- patch["16A"] must always be an array when credit card data is returned.
- patch["16B"] must always be an array when debt data is returned.
- If the uploaded document describes one credit card or one debt, return exactly one object inside the correct array.
- If the uploaded document describes multiple credit cards or debts, return one object per card/debt.
- Keep keys exactly as required by schema.
- Never invent card numbers, account numbers, balances, limits, payments, interest rates, contacts, usernames, passwords, benefits, authorized users, cosigners, or collateral.
- If card/account numbers are masked, copy only the masked value exactly as shown. Never complete or unmask it.
- If only last 4 digits are shown, put them in card_number if it is a credit card.
- For online account credentials, passwords, PINs, or security answers: only copy values if clearly present in the uploaded document.
- If the document only says where credentials or documents are stored, copy that storage location or note into the relevant field.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

16A Credit Card rules:

card_type normalization:
Use one of these values only if clearly supported:
- Visa
- MasterCard
- American Express
- Discover
- Store Card
- Business Card
- Other

If the card type is clearly present but does not match the list:
- card_type = "Other"
- card_type_other = the actual card type from the document

16A field meanings:
- card_name = card name, issuing bank, credit card company, or card brand.
- card_type = normalized credit card type.
- card_type_other = custom card type when card_type is Other.
- card_number = last 4 digits or masked card number exactly as shown.
- account_number = full or masked account number exactly as shown, only if clearly present.
- credit_limit = credit limit, spending limit, or available credit limit.
- current_balance = current balance, statement balance, amount owed, or approximate balance.
- monthly_payment = minimum payment, typical payment, autopay amount, or monthly payment.
- autopay_setup = autopay bank account, payment amount, due date, payment method, or autopay notes.
- card_benefits = rewards, cash back, points, miles, travel benefits, insurance benefits, or perks.
- customer_service = customer service phone, website, address, contact card, or support information.
- online_account = username/password only if clearly shown, or location/note where online account access is stored.
- authorized_users = authorized users, additional cardholders, employee cardholders, or joint users.
- card_documents = statements, agreements, terms, card photos, document location, or upload notes.

16B Other Debt rules:

debt_type normalization:
Use one of these values only if clearly supported:
- Personal Loan
- Student Loan
- Auto Loan
- Home Equity Loan
- Line of Credit
- Medical Debt
- Tax Debt
- Business Loan
- Other

If the debt type is clearly present but does not match the list:
- debt_type = "Other"
- debt_type_other = the actual debt type from the document

16B field meanings:
- debt_type = normalized debt category.
- debt_type_other = custom debt type when debt_type is Other.
- creditor_name = creditor, lender, loan servicer, collection agency, government agency, hospital, school, bank, or financing company.
- account_number = account number, loan number, case number, or masked account number exactly as shown.
- current_balance = balance owed, payoff balance, principal balance, statement balance, or amount due.
- monthly_payment = required monthly payment, minimum payment, installment amount, or regular payment.
- payment_due_date = due date, day of month, next payment date, or payment schedule.
- interest_rate = APR, interest rate, finance charge rate, or variable/fixed rate note.
- payment_method = autopay, check, bank transfer, online payment, payroll deduction, payment account, or payment notes.
- cosigners = co-signers, joint borrowers, guarantors, or responsible parties.
- collateral = secured property such as car, home, equipment, business asset, or collateral description.
- creditor_contact = lender phone, address, website, contact person, email, or uploaded contact details.
- debt_documents = loan agreement, statement, promissory note, payment record, tax notice, document location, or upload notes.

Common source documents:
- credit card statement
- credit card agreement
- card photo or screenshot
- online card account screenshot
- loan statement
- personal loan document
- student loan statement
- auto loan statement
- home equity loan document
- line of credit statement
- medical bill
- tax notice
- business loan document
- payment record
- collection notice
"""


async def extract_section16_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
):
    if subsection and subsection not in VALID_SECTION16_SUBSECTIONS:
        raise ValueError(f"Invalid Section 16 subsection: {subsection}")

    prompt = SECTION16_PROMPT + f"""

Requested section: credit_cards_debt
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "credit_cards_debt"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

If subsection is "16A", only return credit card data inside patch["16A"].
If subsection is "16B", only return debt data inside patch["16B"].
If subsection is FULL_SECTION, return both patch["16A"] and patch["16B"] if found.

Required 16A patch shape:
{{
  "16A": [
    {{
      "card_name": null,
      "card_type": null,
      "card_type_other": null,
      "card_number": null,
      "account_number": null,
      "credit_limit": null,
      "current_balance": null,
      "monthly_payment": null,
      "autopay_setup": null,
      "card_benefits": null,
      "customer_service": null,
      "online_account": null,
      "authorized_users": null,
      "card_documents": null
    }}
  ]
}}

Required 16B patch shape:
{{
  "16B": [
    {{
      "debt_type": null,
      "debt_type_other": null,
      "creditor_name": null,
      "account_number": null,
      "current_balance": null,
      "monthly_payment": null,
      "payment_due_date": null,
      "interest_rate": null,
      "payment_method": null,
      "cosigners": null,
      "collateral": null,
      "creditor_contact": null,
      "debt_documents": null
    }}
  ]
}}

If no information is found for the requested subsection:
- for 16A return {{"16A": []}}
- for 16B return {{"16B": []}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        response_schema=SECTION16_FULL_SCHEMA,
    )