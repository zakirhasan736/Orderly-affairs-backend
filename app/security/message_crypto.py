from __future__ import annotations

from typing import Any

from app.security.crypto import decrypt_data, encrypt_data

MESSAGE_METADATA_KEYS = frozenset({
    "_id",
    "owner_id",
    "recipient_email",
    "message_type",
    "media",
    "delivery_trigger",
    "delivery_date",
    "delivery_occasion",
    "status",
    "is_deleted",
    "sent_at",
    "created_at",
    "updated_at",
    "encrypted_payload",
    "encryption_version",
})

MESSAGE_SENSITIVE_KEYS = (
    "title",
    "subject",
    "content",
    "recipient",
)


def _encryption_context(owner_id: str) -> str:
    return f"message:{owner_id}"


def _extract_sensitive(doc: dict[str, Any]) -> dict[str, Any]:
    sensitive: dict[str, Any] = {}

    encrypted_payload = doc.get("encrypted_payload")
    if encrypted_payload:
        owner_id = str(doc.get("owner_id") or "")
        try:
            sensitive = decrypt_data(encrypted_payload, context=_encryption_context(owner_id))
        except Exception:
            sensitive = decrypt_data(encrypted_payload)

    for key in MESSAGE_SENSITIVE_KEYS:
        if doc.get(key) is not None and key not in sensitive:
            sensitive[key] = doc[key]

    return sensitive


def prepare_message_for_storage(doc: dict[str, Any]) -> dict[str, Any]:
    owner_id = str(doc.get("owner_id") or "")
    sensitive = _extract_sensitive(doc)

    stored = {
        key: value
        for key, value in doc.items()
        if key in MESSAGE_METADATA_KEYS and key != "encrypted_payload"
    }

    if sensitive:
        stored["encrypted_payload"] = encrypt_data(
            sensitive,
            context=_encryption_context(owner_id),
        )
        stored["encryption_version"] = 2

    return stored


def load_message(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return doc

    sensitive = _extract_sensitive(doc)
    result = {
        key: value
        for key, value in doc.items()
        if key in MESSAGE_METADATA_KEYS and key != "encrypted_payload"
    }
    result.update(sensitive)
    return result
