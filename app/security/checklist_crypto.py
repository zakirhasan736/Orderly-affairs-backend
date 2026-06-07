from __future__ import annotations

from typing import Any

from app.security.crypto import decrypt_data, encrypt_data


def _encryption_context(owner_id: str, nextkin_id: str, section_id: str) -> str:
    return f"checklist:{owner_id}:{nextkin_id}:{section_id}"


def prepare_checklist_for_storage(
    *,
    owner_id: str,
    nextkin_id: str,
    section_id: str,
    items: list[Any],
) -> dict[str, Any]:
    return {
        "encrypted_items": encrypt_data(
            {"items": items},
            context=_encryption_context(owner_id, nextkin_id, section_id),
        ),
        "encryption_version": 2,
    }


def load_checklist_items(doc: dict[str, Any] | None) -> list[Any]:
    if not doc:
        return []

    encrypted_items = doc.get("encrypted_items")
    if encrypted_items:
        owner_id = str(doc.get("owner_id") or "")
        nextkin_id = str(doc.get("nextkin_id") or "")
        section_id = str(doc.get("section_id") or "")
        context = _encryption_context(owner_id, nextkin_id, section_id)
        try:
            payload = decrypt_data(encrypted_items, context=context)
        except Exception:
            payload = decrypt_data(encrypted_items)
        return payload.get("items") or []

    return doc.get("items") or []
