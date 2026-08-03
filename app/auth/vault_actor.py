"""Resolve vault owner from owner or family collaborator sessions."""

from __future__ import annotations

from bson import ObjectId
from fastapi import HTTPException

from app.auth.access_types import is_family_collaborator
from app.auth.family_access import family_has_dashboard_area
from app.auth.portal_roles import resolve_dashboard_permissions
from app.database import users_collection


async def resolve_actor(decoded: dict) -> dict | None:
    role = decoded.get("role")
    sub = decoded.get("sub")
    if not sub:
        return None
    if role == "owner":
        return await users_collection.find_one({"email": sub, "role": "owner"})
    if role == "nextkin":
        try:
            user = await users_collection.find_one(
                {"_id": ObjectId(sub), "role": "nextkin"}
            )
        except Exception:
            user = None
        if not user:
            user = await users_collection.find_one({"email": sub, "role": "nextkin"})
        return user
    return None


async def resolve_vault_owner(actor: dict) -> dict:
    if actor.get("role") == "owner":
        return actor
    owner_id = actor.get("owner_id")
    if not owner_id:
        raise HTTPException(status_code=404, detail="Owner not found")
    owner = await users_collection.find_one(
        {"_id": ObjectId(str(owner_id)), "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    return owner


def _family_perm(actor: dict, perm: str) -> bool:
    if not is_family_collaborator(actor):
        return False
    return bool(resolve_dashboard_permissions(actor).get(perm))


async def require_owner_or_family(
    decoded: dict,
    *,
    perm: str | None = None,
    area_id: str | None = None,
    detail: str = "Forbidden",
) -> tuple[dict, dict]:
    """
    Return (actor, vault_owner).

    - Owners always pass.
    - Family collaborators need optional role `perm` and/or dashboard `area_id`.
    """
    actor = await resolve_actor(decoded)
    if not actor:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if actor.get("role") == "owner":
        return actor, actor

    if not is_family_collaborator(actor):
        raise HTTPException(status_code=403, detail=detail)

    if not actor.get("immediate_access", False):
        raise HTTPException(status_code=403, detail="Access not approved")

    if perm and not _family_perm(actor, perm):
        raise HTTPException(status_code=403, detail=detail)

    if area_id and not family_has_dashboard_area(actor, area_id):
        raise HTTPException(status_code=403, detail=detail)

    owner = await resolve_vault_owner(actor)
    return actor, owner


async def require_owner_or_family_reader(
    decoded: dict,
    *,
    detail: str = "Forbidden",
) -> tuple[dict, dict]:
    """Owner or any approved family collaborator (for footprints, etc.)."""
    actor = await resolve_actor(decoded)
    if not actor:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if actor.get("role") == "owner":
        return actor, actor

    if not is_family_collaborator(actor):
        raise HTTPException(status_code=403, detail=detail)

    if not actor.get("immediate_access", False):
        raise HTTPException(status_code=403, detail="Access not approved")

    owner = await resolve_vault_owner(actor)
    return actor, owner
