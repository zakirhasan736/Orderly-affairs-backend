"""Project secrets so NOK never receives full numbers, logins, or documents."""

from __future__ import annotations

from typing import Any

FINANCE_SECTIONS = frozenset({"12", "14", "16"})

LAST4_KEYS = frozenset(
    {
        "account_number",
        "policy_number",
        "document_number",
        "drivers_license_number",
        "license_number",
        "dl_number",
    }
)
CREDENTIAL_KEYS = frozenset(
    {
        "routing_number",
        "online_banking",
        "online_banking_password",
        "password",
        "account_password",
        "login_credentials",
        "online_account",
        "security_info",
        "phone_password",
        "voicemail_pin",
        "computer_password",
        "primary_email_password",
        "secondary_email_password",
        "google_id_password",
        "apple_id_password",
        "frequent_pins",
        "safe_code",
        "social_security_number",
    }
)
DOCUMENT_KEYS = frozenset(
    {
        "account_documents",
        "service_documents",
        "card_documents",
        "debit_cards",
        "debt_documents",
        "policy_documents",
        "policy_documents_life",
        "document_upload",
        "tax_documents",
        "military_documents",
        "employment_documents",
        "business_documents",
        "income_documents",
        "item_documents",
        "property_documents",
        "legal_documents",
        "adoption_documents",
        "name_change_documents",
        "contact_documents",
        "paid_off_documentation",
        "item_documentation",
        "documentation",
        "pet_documentation",
        "dependency_documents",
        "relationship_documents",
        "business_tax_documents",
    }
)
IGNORE_KEYS = frozenset(
    {
        "document_type",
        "document_location",
        "document_description",
    }
)


def last_four_digits(value: Any) -> str:
    text = _plain_text(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 4:
        return digits[-4:]
    return ""


def _plain_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, dict):
        if value.get("text") is not None:
            return str(value.get("text") or "")
        if value.get("value") is not None:
            return str(value.get("value") or "")
    return ""


def last4_server_payload(value: Any) -> Any:
    last4 = last_four_digits(value)
    if not last4:
        return ""
    if isinstance(value, dict):
        return {"text": last4}
    return last4


def _is_meta(key: str) -> bool:
    return (
        key.endswith("_instructions")
        or key.endswith("_header")
        or key.endswith("_label")
        or key in IGNORE_KEYS
    )


def classify_field(section_id: str, field_key: str) -> str | None:
    sid = str(section_id or "")
    key = str(field_key or "").strip().lower()
    if not key or _is_meta(key):
        return None
    if (
        key in DOCUMENT_KEYS
        or key.endswith("_documents")
        or key.endswith("_documentation")
        or "upload" in key
    ):
        return "document"
    if (
        key in CREDENTIAL_KEYS
        or "password" in key
        or key.endswith("_pin")
        or "ssn" in key
        or key == "cvv"
    ):
        return "credential"
    if sid in FINANCE_SECTIONS and key == "username":
        return "credential"
    if key in LAST4_KEYS:
        return "secret_last4"
    if key == "card_number":
        return "locator"
    return None


def project_field_for_nok(section_id: str, field_key: str, value: Any) -> Any:
    kind = classify_field(section_id, field_key)
    if kind in {"document", "credential"}:
        return None
    if kind == "secret_last4":
        return last4_server_payload(value)
    return value
