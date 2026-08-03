"""
Resolve who may write a vault section, and attribute updates to an actor.
"""

from __future__ import annotations

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException, Request

from app.auth.portal_roles import can_write_sections, normalize_portal_role, role_label
from app.database import users_collection
from app.security.access_control import assert_section_read_access
from app.security.token_resolver import decode_owner_or_nok_token


def actor_from_user(user: dict) -> dict:
    role = user.get("role") or "owner"
    from app.auth.access_types import is_family_collaborator

    portal = None
    portal_label = "Owner"
    if role == "nextkin" and is_family_collaborator(user):
        portal = normalize_portal_role(user.get("portal_role"))
        portal_label = role_label(portal)
    elif role == "nextkin":
        portal_label = "Next of Kin"

    return {
        "user_id": str(user.get("_id") or ""),
        "email": user.get("email") or "",
        "full_name": user.get("full_name") or user.get("email") or "Unknown",
        "role": role,
        "portal_role": portal,
        "portal_role_label": portal_label,
    }


async def _find_nextkin(decoded: dict) -> dict | None:
    sub = decoded.get("sub")
    user = await users_collection.find_one({"email": sub, "role": "nextkin"})
    if user:
        return user
    try:
        return await users_collection.find_one(
            {"_id": ObjectId(str(sub)), "role": "nextkin"}
        )
    except (InvalidId, TypeError):
        return None


async def require_section_write(
    request: Request,
    authorization: str | None,
    section_id: str,
) -> tuple[dict, dict]:
    """
    Returns (owner_user, actor_meta).

    Owner always may write. Family collaborators may write with editor+ portal
    role and read access. Next-of-Kin are always read-only.
    """
    decoded = decode_owner_or_nok_token(request, authorization)
    role = decoded.get("role")

    if role == "owner":
        owner = await users_collection.find_one(
            {"email": decoded["sub"], "role": "owner"}
        )
        if not owner:
            raise HTTPException(status_code=401, detail="Owner not found")
        return owner, actor_from_user(owner)

    if role != "nextkin":
        raise HTTPException(status_code=403, detail="Invalid role")

    user = await _find_nextkin(decoded)
    if not user:
        raise HTTPException(status_code=401, detail="Collaborator not found")

    assert_section_read_access(user, section_id)
    if not can_write_sections(user):
        from app.auth.access_types import is_family_collaborator

        detail = (
            "Your role is view-only. Ask the kit owner for Editor or higher access."
            if is_family_collaborator(user)
            else "Next-of-Kin access is view-only. Family collaborators can be granted edit roles in Vault Settings."
        )
        raise HTTPException(status_code=403, detail=detail)

    try:
        owner = await users_collection.find_one(
            {"_id": ObjectId(str(user["owner_id"])), "role": "owner"}
        )
    except (InvalidId, TypeError, KeyError):
        owner = None
    if not owner:
        raise HTTPException(status_code=404, detail="Kit owner not found")

    return owner, actor_from_user(user)
