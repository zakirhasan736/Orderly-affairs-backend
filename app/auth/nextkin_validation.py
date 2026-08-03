"""
Pure Next-of-Kin / Access Management validation helpers.

Used by create/update routes and unit tests so empty required fields
are rejected before Mongo writes.
"""

from __future__ import annotations

from typing import Any


FULL_KIT_ACCESS = "Full Kit Access"
SECTION_SPECIFIC_ACCESS = "Section-Specific Access"
MAX_NOK_AUTHORIZED_SECTIONS = 5


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def normalize_access_level(value: str | None) -> str:
    raw = (value or "").strip()
    if raw in (SECTION_SPECIFIC_ACCESS, "section", "limited", "section-specific"):
        return SECTION_SPECIFIC_ACCESS
    if raw in (FULL_KIT_ACCESS, "full", "full_kit", ""):
        return FULL_KIT_ACCESS
    # Preserve known UI labels; treat unknown as full kit for safety
    if "section" in raw.lower():
        return SECTION_SPECIFIC_ACCESS
    return FULL_KIT_ACCESS


def validate_nextkin_required_fields(
    *,
    full_name: str | None,
    email: str | None,
    relationship: str | None,
    access_level: str | None = None,
    authorized_sections: list[str] | None = None,
    master_password: str | None = None,
    require_password: bool = True,
) -> str | None:
    """
    Return an error message when required Access Management fields are missing,
    otherwise None.
    """
    if _is_blank(full_name):
        return "Full name is required"
    if _is_blank(email):
        return "Email is required"
    if _is_blank(relationship):
        return "Relationship is required"

    level = normalize_access_level(access_level)
    sections = [s for s in (authorized_sections or []) if str(s).strip()]
    if level == SECTION_SPECIFIC_ACCESS and not sections:
        return "Select at least one section for section-specific access"
    if level == SECTION_SPECIFIC_ACCESS and len(sections) > MAX_NOK_AUTHORIZED_SECTIONS:
        return (
            f"Select at most {MAX_NOK_AUTHORIZED_SECTIONS} sections "
            "for Next-of-Kin section-specific access"
        )

    if require_password and _is_blank(master_password):
        return "Master password is required"

    return None


def prepare_nextkin_create_fields(payload: Any) -> dict[str, Any]:
    """Normalize create payload fields used by /create-nextkin."""
    access_level = normalize_access_level(getattr(payload, "access_level", None))
    sections = list(getattr(payload, "authorized_sections", None) or [])
    if access_level == FULL_KIT_ACCESS:
        # Full kit does not need a partial section list
        sections = sections or []

    error = validate_nextkin_required_fields(
        full_name=getattr(payload, "full_name", None),
        email=str(getattr(payload, "email", "") or ""),
        relationship=getattr(payload, "relationship", None),
        access_level=access_level,
        authorized_sections=sections,
        master_password=getattr(payload, "master_password", None),
        # Backend may generate a temp password when omitted
        require_password=False,
    )
    if error:
        raise ValueError(error)

    return {
        "full_name": str(payload.full_name).strip(),
        "email": str(payload.email).strip().lower(),
        "relationship": str(payload.relationship).strip(),
        "access_level": access_level,
        "authorized_sections": sections if access_level == SECTION_SPECIFIC_ACCESS else sections,
    }
