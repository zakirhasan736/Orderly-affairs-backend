"""
Collaborator access types on users with role=nextkin.

- nextkin: Section 2 Next-of-Kin (immediate / upon-death kit access, read-only)
- family: Vault Settings family collaborators (portal roles + section write)
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

ACCESS_TYPE_NEXTKIN = "nextkin"
ACCESS_TYPE_FAMILY = "family"
MAX_NEXTKIN_PER_OWNER = 5
MAX_FAMILY_PER_OWNER = 5
MAX_NOK_AUTHORIZED_SECTIONS = 5

# Mongo filter: legacy docs without access_type are treated as nextkin.
NEXTKIN_ACCESS_MONGO_FILTER: dict[str, Any] = {
    "$or": [
        {"access_type": {"$exists": False}},
        {"access_type": None},
        {"access_type": ACCESS_TYPE_NEXTKIN},
    ]
}

FAMILY_ACCESS_MONGO_FILTER: dict[str, Any] = {"access_type": ACCESS_TYPE_FAMILY}


def normalize_access_type(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    if value == ACCESS_TYPE_FAMILY:
        return ACCESS_TYPE_FAMILY
    return ACCESS_TYPE_NEXTKIN


def resolve_access_type(user: dict | None) -> str:
    if not user:
        return ACCESS_TYPE_NEXTKIN
    return normalize_access_type(user.get("access_type"))


def is_family_collaborator(user: dict | None) -> bool:
    return resolve_access_type(user) == ACCESS_TYPE_FAMILY


def is_nextkin_collaborator(user: dict | None) -> bool:
    return resolve_access_type(user) == ACCESS_TYPE_NEXTKIN


LOGIN_PORTAL_NEXTKIN = "nextkin"
LOGIN_PORTAL_FAMILY = "family"

WRONG_PORTAL_USE_FAMILY = (
    "This account signs in at the family collaborator page, not Next of Kin."
)
WRONG_PORTAL_USE_NEXTKIN = (
    "This account signs in at the Next of Kin page, not family collaborator."
)


def require_collaborator_login_portal(user: dict | None, expected: str | None) -> None:
    """Reject NOK credentials on the family gate and family credentials on NOK."""
    portal = str(expected or "").strip().lower()
    if not portal:
        return
    if portal not in (LOGIN_PORTAL_NEXTKIN, LOGIN_PORTAL_FAMILY):
        raise HTTPException(status_code=400, detail="Invalid login portal")

    actual = resolve_access_type(user)
    if portal == LOGIN_PORTAL_FAMILY and actual != ACCESS_TYPE_FAMILY:
        raise HTTPException(status_code=403, detail=WRONG_PORTAL_USE_NEXTKIN)
    if portal == LOGIN_PORTAL_NEXTKIN and actual == ACCESS_TYPE_FAMILY:
        raise HTTPException(status_code=403, detail=WRONG_PORTAL_USE_FAMILY)


def validate_nok_authorized_sections(
    access_level: str | None,
    authorized_sections: list[str] | None,
) -> list[str]:
    """Normalize and enforce max 5 sections for NOK section-specific access."""
    level = (access_level or "").strip()
    sections = [str(s).strip() for s in (authorized_sections or []) if str(s).strip()]
    if level == "Section-Specific Access":
        if len(sections) > MAX_NOK_AUTHORIZED_SECTIONS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Next-of-Kin section-specific access allows at most "
                    f"{MAX_NOK_AUTHORIZED_SECTIONS} sections"
                ),
            )
        return sections
    return sections
