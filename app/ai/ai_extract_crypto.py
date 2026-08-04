"""Encrypt / decrypt AI document extracted text at rest."""

from __future__ import annotations

from app.security.crypto import decrypt_data, encrypt_data, is_encrypted_payload


def ai_extract_context(user_id: str, file_id: str) -> str:
    return f"ai_extract:{user_id}:{file_id}"


def encrypt_extracted_text(*, user_id: str, file_id: str, text: str) -> str:
    """Store OCR / extract text as AES-256-GCM ciphertext (empty → empty)."""
    clean = (text or "")[:50000]
    if not clean:
        return ""
    return encrypt_data(
        {"text": clean},
        context=ai_extract_context(str(user_id), str(file_id)),
    )


def read_extracted_text(doc: dict | None) -> str:
    """
    Read extracted text from an ai_documents row.

    Supports new encrypted payloads and legacy plaintext rows.
    """
    if not isinstance(doc, dict):
        return ""

    raw = doc.get("extracted_text")
    if raw is None or raw == "":
        return ""

    text = str(raw)
    if not is_encrypted_payload(text):
        return text[:50000]

    user_id = str(doc.get("user_id") or doc.get("owner_id") or "")
    file_id = str(doc.get("_id") or "")
    try:
        payload = decrypt_data(
            text,
            context=ai_extract_context(user_id, file_id),
        )
        return str(payload.get("text") or "")[:50000]
    except Exception:
        # Legacy / mismatched context — try unbound decrypt then give up.
        try:
            payload = decrypt_data(text)
            return str(payload.get("text") or "")[:50000]
        except Exception:
            return ""
