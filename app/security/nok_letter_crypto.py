from __future__ import annotations

from typing import Any

from app.security.crypto import decrypt_data, encrypt_data

# Query/delivery metadata kept plaintext for scheduling and email routing.
NOK_LETTER_METADATA_KEYS = frozenset({
    "_id",
    "owner_id",
    "nok_user_id",
    "delivery_trigger",
    "delivery_status",
    "scheduled_send_at",
    "sent_at",
    "nok_email",
    "letter_date",
    "created_at",
    "updated_at",
    "last_delivery_error",
    "encrypted_payload",
    "encryption_version",
})


def _encryption_context(owner_id: str, nok_user_id: str | None) -> str:
    return f"nok_letter:{owner_id}:{nok_user_id or 'default'}"


def prepare_nok_letter_for_storage(doc: dict[str, Any]) -> dict[str, Any]:
    owner_id = str(doc.get("owner_id") or "")
    nok_user_id = str(doc.get("nok_user_id")) if doc.get("nok_user_id") else None

    stored = {
        key: value
        for key, value in doc.items()
        if key in NOK_LETTER_METADATA_KEYS and key != "encrypted_payload"
    }

    sensitive = {
        key: value
        for key, value in doc.items()
        if key not in NOK_LETTER_METADATA_KEYS and value is not None
    }

    if sensitive:
        stored["encrypted_payload"] = encrypt_data(
            sensitive,
            context=_encryption_context(owner_id, nok_user_id),
        )
        stored["encryption_version"] = 2

    return stored


def load_nok_letter(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return doc

    encrypted_payload = doc.get("encrypted_payload")
    if not encrypted_payload:
        return doc

    owner_id = str(doc.get("owner_id") or "")
    nok_user_id = str(doc.get("nok_user_id")) if doc.get("nok_user_id") else None
    context = _encryption_context(owner_id, nok_user_id)

    try:
        sensitive = decrypt_data(encrypted_payload, context=context)
    except Exception:
        sensitive = decrypt_data(encrypted_payload)

    result = {
        key: value
        for key, value in doc.items()
        if key in NOK_LETTER_METADATA_KEYS and key != "encrypted_payload"
    }
    result.update(sensitive)
    return result
