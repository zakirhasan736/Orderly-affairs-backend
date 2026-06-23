from __future__ import annotations

from app.database import section_data_collection
from app.security.section_crypto import decrypt_section_data


def _clean_name(value: object) -> str | None:
    if not value or not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _name_from_section_data(data: dict | None) -> str | None:
    if not data:
        return None

    vital = data.get("vital_info")
    if isinstance(vital, dict):
        legal_name = _clean_name(vital.get("full_legal_name"))
        if legal_name:
            return legal_name

    return _clean_name(data.get("full_legal_name"))


async def resolve_owner_display_name(owner: dict | None) -> str:
    """Prefer the kit owner's real name — never their email in outbound copy."""
    if not owner:
        return "Your kit owner"

    name = _clean_name(owner.get("full_name"))
    if name:
        return name

    owner_id = str(owner.get("_id") or "")
    if owner_id:
        try:
            section = await section_data_collection.find_one({
                "owner_id": owner_id,
                "section_id": "1",
            })
            if section and section.get("encrypted_data"):
                data = decrypt_section_data(
                    owner_id,
                    "1",
                    section["encrypted_data"],
                )
                legal_name = _name_from_section_data(data)
                if legal_name:
                    return legal_name
        except Exception:
            pass

    return "Your kit owner"


def resolve_nextkin_display_name(nextkin: dict | None) -> str:
    if not nextkin:
        return "there"

    return (
        _clean_name(nextkin.get("full_name"))
        or _clean_name(nextkin.get("email"))
        or "there"
    )
