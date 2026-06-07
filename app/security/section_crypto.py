from __future__ import annotations

from app.security.crypto import decrypt_data, encrypt_data, is_encrypted_payload


def section_encryption_context(owner_id: str, section_id: str) -> str:
    return f"section:{owner_id}:{section_id}"


def encrypt_section_data(owner_id: str, section_id: str, data: dict) -> str:
    return encrypt_data(data, context=section_encryption_context(owner_id, section_id))


def decrypt_section_data(owner_id: str, section_id: str, encrypted_data: str) -> dict:
    if not encrypted_data:
        return {}

    context = section_encryption_context(owner_id, section_id)
    try:
        return decrypt_data(encrypted_data, context=context)
    except Exception:
        return decrypt_data(encrypted_data)
