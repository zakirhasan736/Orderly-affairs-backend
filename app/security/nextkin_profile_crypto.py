from __future__ import annotations

from typing import Any

from app.security.crypto import decrypt_data, encrypt_data

PROFILE_SENSITIVE_KEYS = (
    "master_password",
    "card_storage_location",
    "key_bag_location",
    "documents_bag_location",
    "special_instructions",
)


def _encryption_context(owner_id: str, nextkin_id: str) -> str:
    return f"nextkin_profile:{owner_id}:{nextkin_id}"


def _resolve_nextkin_id(doc: dict[str, Any]) -> str:
    # Email is stable at creation time; prefer it so encrypt/decrypt contexts match.
    return str(doc.get("email") or doc.get("_id") or doc.get("id") or "pending")


def _read_sensitive(doc: dict[str, Any]) -> dict[str, Any]:
    sensitive: dict[str, Any] = {}
    encrypted_profile = doc.get("encrypted_profile")

    if encrypted_profile:
        owner_id = str(doc.get("owner_id") or "")
        nextkin_id = _resolve_nextkin_id(doc)
        context = _encryption_context(owner_id, nextkin_id)
        try:
            sensitive = decrypt_data(encrypted_profile, context=context)
        except Exception:
            sensitive = decrypt_data(encrypted_profile)

    for key in PROFILE_SENSITIVE_KEYS:
        if doc.get(key) is not None and key not in sensitive:
            sensitive[key] = doc[key]

    return sensitive


def prepare_nextkin_profile_for_storage(doc: dict[str, Any]) -> dict[str, Any]:
    owner_id = str(doc.get("owner_id") or "")
    nextkin_id = _resolve_nextkin_id(doc)
    sensitive = _read_sensitive(doc)

    stored = {
        key: value
        for key, value in doc.items()
        if key not in PROFILE_SENSITIVE_KEYS and key != "encrypted_profile"
    }

    if sensitive:
        stored["encrypted_profile"] = encrypt_data(
            sensitive,
            context=_encryption_context(owner_id, nextkin_id),
        )
        stored["encryption_version"] = 2

    return stored


def load_nextkin_profile(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not doc:
        return doc

    sensitive = _read_sensitive(doc)
    result = {
        key: value
        for key, value in doc.items()
        if key not in PROFILE_SENSITIVE_KEYS and key != "encrypted_profile"
    }
    result.update(sensitive)
    return result
