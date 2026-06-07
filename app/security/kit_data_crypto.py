from __future__ import annotations

from typing import Any

from app.security.crypto import decrypt_data, encrypt_data, is_encrypted_payload


def _section_context(owner_id: str, section_id: str) -> str:
    return f"kit:{owner_id}:section:{section_id}"


def _subsection_context(owner_id: str, subsection_id: str) -> str:
    return f"kit:{owner_id}:subsection:{subsection_id}"


def encrypt_kit_section_data(owner_id: str, section_id: str, data: Any) -> str:
    payload = data if isinstance(data, dict) else {"value": data}
    return encrypt_data(payload, context=_section_context(owner_id, section_id))


def encrypt_kit_subsection_data(owner_id: str, subsection_id: str, data: Any) -> str:
    payload = data if isinstance(data, dict) else {"value": data}
    return encrypt_data(payload, context=_subsection_context(owner_id, subsection_id))


def _decrypt_payload(owner_id: str, context: str, encrypted_data: str) -> Any:
    try:
        payload = decrypt_data(encrypted_data, context=context)
    except Exception:
        payload = decrypt_data(encrypted_data)

    if isinstance(payload, dict) and set(payload.keys()) == {"value"}:
        return payload["value"]
    return payload


def load_kit_section(owner_id: str, section: dict[str, Any]) -> dict[str, Any]:
    encrypted_data = section.get("encrypted_data")
    if encrypted_data and is_encrypted_payload(encrypted_data):
        section["data"] = _decrypt_payload(
            owner_id,
            _section_context(owner_id, str(section.get("id") or "")),
            encrypted_data,
        )
    return section


def load_kit_subsection(owner_id: str, subsection: dict[str, Any]) -> dict[str, Any]:
    encrypted_data = subsection.get("encrypted_data")
    if encrypted_data and is_encrypted_payload(encrypted_data):
        subsection["data"] = _decrypt_payload(
            owner_id,
            _subsection_context(owner_id, str(subsection.get("id") or "")),
            encrypted_data,
        )
    return subsection


def load_kit_document(kit: dict[str, Any] | None) -> dict[str, Any] | None:
    if not kit:
        return kit

    owner_id = str(kit.get("owner_id") or "")
    if not owner_id:
        return kit

    for section in kit.get("sections", []):
        load_kit_section(owner_id, section)
        for subsection in section.get("subsections", []):
            load_kit_subsection(owner_id, subsection)

    return kit


def prepare_kit_section_for_storage(
    owner_id: str,
    section_id: str,
    data: Any,
) -> dict[str, Any]:
    return {
        "encrypted_data": encrypt_kit_section_data(owner_id, section_id, data),
        "encryption_version": 2,
    }


def prepare_kit_subsection_for_storage(
    owner_id: str,
    subsection_id: str,
    data: Any,
) -> dict[str, Any]:
    return {
        "encrypted_data": encrypt_kit_subsection_data(owner_id, subsection_id, data),
        "encryption_version": 2,
    }
