from app.ai.extractors.base_extractor import extract_structured_from_document
from app.ai.schemas.section13_schema import SECTION13_FULL_SCHEMA

VALID_SECTION13_SUBSECTIONS = {
    "13A",
}

SECTION13_PROMPT = """
You are extracting data for the 'Passwords & Online Accounts' section of an estate planning app.

Return JSON only.
Do not guess.
Only include values clearly supported by the uploaded document.
If a field is not present, use null for scalar fields and [] for arrays.

Important rules:
- The only supported subsection is 13A.
- 13A means Online Accounts.
- patch["13A"] must always be an array.
- If the uploaded document describes one online account, return exactly one object inside patch["13A"].
- If the uploaded document describes multiple online accounts, return one object per account inside patch["13A"].
- Keep keys exactly as required by schema.
- Never invent usernames, passwords, emails, phone numbers, recovery details, 2FA details, security answers, backup codes, account values, or closure instructions.
- If passwords, security answers, or backup codes are masked, copy only the masked value exactly as shown. Never try to complete or unmask it.
- If the document only says where credentials are stored, copy that storage location or note into account_password, recovery_info, two_factor_auth, or account_documents depending on context.
- If a value is unclear, return null.
- Do not include markdown.
- Do not explain.

Account type normalization:
Use one of these values only if clearly supported:
- Social Media
- Email
- Banking
- Investment
- Shopping
- Streaming
- Cloud Storage
- Work/Professional
- Government
- Utilities
- Other

If the account type is clearly present but does not match the list:
- account_type = "Other"
- account_type_other = the actual account type from the document

Field meanings:
- account_type = normalized category of online account.
- account_type_other = custom account type when account_type is Other.
- service_name = website, app, platform, service, or company name, such as Facebook, Gmail, Amazon, Netflix, Dropbox, IRS, utility portal, etc.
- account_username = username, login ID, handle, user ID, customer ID, account login, or merchant ID.
- account_password = password only if clearly present, or a note/location describing where the password is stored.
- email_associated = email address connected to the account.
- phone_associated = phone number connected to the account.
- recovery_info = backup email, recovery phone, security questions, recovery codes, account recovery notes, or where recovery info is stored.
- two_factor_auth = 2FA method, authenticator app, SMS/email 2FA, security key, backup codes, recovery code storage, or trusted devices.
- account_value = financial value, personal importance, business importance, subscription importance, stored files/photos, customer data, domain ownership, or other account significance.
- closure_instructions = instructions for closing, deleting, memorializing, transferring, preserving, or notifying contacts about the account.
- account_documents = screenshots, account statements, login info sheet, password manager export note, storage location, upload note, or related documents.

Common source documents:
- password manager export
- account inventory document
- online account list
- screenshots showing account settings
- email account details
- social media account settings
- cloud storage account details
- streaming or subscription account records
- shopping account records
- government portal information
- utility portal account information
- business/professional account credentials document
"""


async def extract_section13_from_document(
    document_url: str,
    subsection: str | None = None,
    mime_type: str = "application/pdf",
):
    if subsection and subsection not in VALID_SECTION13_SUBSECTIONS:
        raise ValueError(f"Invalid Section 13 subsection: {subsection}")

    prompt = SECTION13_PROMPT + f"""

Requested section: passwords_online_accounts
Requested subsection: {subsection or "FULL_SECTION"}

Return this exact JSON structure:
- section: "passwords_online_accounts"
- scope: {"subsection" if subsection else "section"}
- subsection: {subsection if subsection else "null"}
- confidence: number between 0 and 1
- patch: extracted values only

Required patch shape:
{{
  "13A": [
    {{
      "account_type": null,
      "account_type_other": null,
      "service_name": null,
      "account_username": null,
      "account_password": null,
      "email_associated": null,
      "phone_associated": null,
      "recovery_info": null,
      "two_factor_auth": null,
      "account_value": null,
      "closure_instructions": null,
      "account_documents": null
    }}
  ]
}}

If no online account information is found:
{{
  "13A": []
}}
"""

    return await extract_structured_from_document(
        document_url=document_url,
        mime_type=mime_type,
        prompt=prompt,
        response_schema=SECTION13_FULL_SCHEMA,
    )