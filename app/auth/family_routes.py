"""Family collaborator CRUD — Vault Settings (separate from Section 2 NOK)."""

from __future__ import annotations

from datetime import datetime

from bson import ObjectId
from fastapi import HTTPException, Request

from app.auth.access_types import ACCESS_TYPE_FAMILY, FAMILY_ACCESS_MONGO_FILTER
from app.auth.family_access import prepare_family_access_fields
from app.auth.family_schemas import FamilyCreateRequest, FamilyUpdateRequest
from app.auth.portal_roles import (
    normalize_portal_role,
    resolve_dashboard_permissions,
    role_label,
)
from app.database import users_collection
from app.notifications.nextkin_emails import send_family_invite_email
from app.security.nextkin_profile_crypto import (
    load_nextkin_profile,
    prepare_nextkin_profile_for_storage,
)
from app.security.password_handler import hash_password
from app.security.token_resolver import decode_access_token
from app.security.usage_guard import enforce_usage


def _require_owner_or_family_manager(decoded: dict, actor: dict | None) -> None:
    if decoded.get("role") == "owner":
        return
    if (
        decoded.get("role") == "nextkin"
        and actor
        and resolve_dashboard_permissions(actor).get("can_manage_family_access")
    ):
        return
    raise HTTPException(
        status_code=403,
        detail="Only the owner or a Portal Manager+ can manage family access",
    )


async def _resolve_actor(decoded: dict) -> dict | None:
    if decoded.get("role") == "owner":
        return await users_collection.find_one(
            {"email": decoded["sub"], "role": "owner"}
        )
    if decoded.get("role") == "nextkin":
        try:
            return await users_collection.find_one(
                {"_id": ObjectId(decoded["sub"]), "role": "nextkin"}
            )
        except Exception:
            return await users_collection.find_one(
                {"email": decoded.get("sub"), "role": "nextkin"}
            )
    return None


async def _resolve_owner_for_actor(actor: dict) -> dict:
    if actor.get("role") == "owner":
        return actor
    owner = await users_collection.find_one(
        {"_id": ObjectId(str(actor["owner_id"])), "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    return owner


async def create_family_member(
    payload: FamilyCreateRequest,
    request: Request,
    authorization: str | None,
    *,
    generate_password,
):
    decoded = decode_access_token(request, authorization)
    actor = await _resolve_actor(decoded)
    if not actor:
        raise HTTPException(status_code=401, detail="Unauthorized")
    _require_owner_or_family_manager(decoded, actor)
    owner = await _resolve_owner_for_actor(actor)

    count = await users_collection.count_documents(
        {
            "owner_id": str(owner["_id"]),
            "role": "nextkin",
            **FAMILY_ACCESS_MONGO_FILTER,
        }
    )
    enforce_usage(owner, "family", count)

    try:
        normalized = prepare_family_access_fields(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    email = normalized["email"]
    existing = await users_collection.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="A user with this email already exists")

    plain_password = (payload.master_password or "").strip() or generate_password()
    portal_role = normalize_portal_role(payload.portal_role)
    temp_user = {"role": "nextkin", "portal_role": portal_role, "access_type": ACCESS_TYPE_FAMILY}
    if payload.dashboard_permissions:
        temp_user["dashboard_permissions"] = payload.dashboard_permissions
    permissions = resolve_dashboard_permissions(temp_user)

    new_doc = {
        "email": email,
        "full_name": normalized["full_name"],
        "relationship": normalized["relationship"],
        "phone_number": payload.phone_number,
        "access_level": normalized["access_level"],
        "authorized_sections": normalized["authorized_sections"] or [],
        "access_type": ACCESS_TYPE_FAMILY,
        "portal_role": portal_role,
        "dashboard_permissions": permissions,
        "immediate_access": True,
        "access_timing": "immediate",
        "access_revoked": False,
        "nok_letter_received": False,
        "password_card_generated": False,
        "master_password": plain_password,
        "password_hash": hash_password(plain_password),
        "role": "nextkin",
        "owner_id": str(owner["_id"]),
        "verified": True,
        "mfa_enabled": False,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    stored = prepare_nextkin_profile_for_storage(new_doc)
    insert_res = await users_collection.insert_one(stored)
    family = load_nextkin_profile(
        await users_collection.find_one({"_id": insert_res.inserted_id})
    )

    await send_family_invite_email(
        family=family,
        owner=owner,
        plain_password=plain_password,
    )

    return {
        "message": f"Family member '{payload.full_name}' invited successfully.",
        "email": email,
        "relationship": payload.relationship,
        "id": str(insert_res.inserted_id),
        "portal_role": portal_role,
        "portal_role_label": role_label(portal_role),
        "dashboard_permissions": permissions,
        "temp_password_sent": True,
        "master_password": plain_password,
    }


async def list_family_members(request: Request, authorization: str | None):
    decoded = decode_access_token(request, authorization)
    actor = await _resolve_actor(decoded)
    if not actor:
        raise HTTPException(status_code=401, detail="Unauthorized")
    _require_owner_or_family_manager(decoded, actor)
    owner = await _resolve_owner_for_actor(actor)

    cursor = users_collection.find(
        {
            "owner_id": str(owner["_id"]),
            "role": "nextkin",
            **FAMILY_ACCESS_MONGO_FILTER,
        }
    )
    results = []
    async for doc in cursor:
        nk = load_nextkin_profile(doc)
        results.append(
            {
                "id": str(nk["_id"]),
                "email": nk["email"],
                "full_name": nk.get("full_name"),
                "relationship": nk.get("relationship"),
                "phone_number": nk.get("phone_number"),
                "access_level": nk.get("access_level"),
                "access_level_label": (
                    "Full Dashboard Access"
                    if nk.get("access_level") == "Full Kit Access"
                    else "Area-Specific Access"
                ),
                "authorized_sections": nk.get("authorized_sections", []),
                "access_type": ACCESS_TYPE_FAMILY,
                "portal_role": nk.get("portal_role") or "viewer",
                "portal_role_label": role_label(nk.get("portal_role")),
                "dashboard_permissions": resolve_dashboard_permissions(nk),
                "immediate_access": True,
                "has_master_password": bool(
                    nk.get("password_hash") or nk.get("master_password")
                ),
                "master_password": nk.get("master_password") or "",
                "created_at": nk.get("created_at"),
                "updated_at": nk.get("updated_at"),
            }
        )
    return results


async def update_family_member(
    family_id: str,
    payload: FamilyUpdateRequest,
    request: Request,
    authorization: str | None,
):
    decoded = decode_access_token(request, authorization)
    actor = await _resolve_actor(decoded)
    if not actor:
        raise HTTPException(status_code=401, detail="Unauthorized")
    _require_owner_or_family_manager(decoded, actor)
    owner = await _resolve_owner_for_actor(actor)

    family = await users_collection.find_one(
        {
            "_id": ObjectId(family_id),
            "role": "nextkin",
            "owner_id": str(owner["_id"]),
            **FAMILY_ACCESS_MONGO_FILTER,
        }
    )
    if not family:
        raise HTTPException(status_code=404, detail="Family member not found")

    current = load_nextkin_profile(dict(family)) or dict(family)
    previous_password = current.get("master_password")
    update_data = {k: v for k, v in payload.dict().items() if v is not None}

    if "portal_role" in update_data:
        update_data["portal_role"] = normalize_portal_role(update_data.get("portal_role"))

    if "access_level" in update_data or "authorized_sections" in update_data:
        class _Tmp:
            pass

        tmp = _Tmp()
        tmp.full_name = update_data.get("full_name") or current.get("full_name")
        tmp.email = update_data.get("email") or current.get("email")
        tmp.relationship = update_data.get("relationship") or current.get(
            "relationship"
        )
        tmp.access_level = update_data.get("access_level") or current.get(
            "access_level"
        )
        tmp.authorized_sections = update_data.get(
            "authorized_sections", current.get("authorized_sections")
        )
        try:
            normalized = prepare_family_access_fields(tmp)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        update_data["access_level"] = normalized["access_level"]
        update_data["authorized_sections"] = normalized["authorized_sections"]

    update_data["access_type"] = ACCESS_TYPE_FAMILY
    update_data["immediate_access"] = True
    update_data["access_timing"] = "immediate"

    merged_for_perms = dict(current)
    merged_for_perms.update(update_data)
    update_data["dashboard_permissions"] = resolve_dashboard_permissions(
        merged_for_perms
    )

    password_changed = False
    new_password = (payload.master_password or "").strip() or None
    if new_password and new_password != (previous_password or ""):
        password_changed = True
        update_data["password_hash"] = hash_password(new_password)
        update_data["master_password"] = new_password
    elif "master_password" in update_data and not new_password:
        update_data.pop("master_password", None)

    if not update_data:
        raise HTTPException(status_code=400, detail="No valid fields provided to update")

    merged = dict(current)
    merged.update(update_data)
    merged["owner_id"] = str(owner["_id"])
    merged["_id"] = family["_id"]
    stored = prepare_nextkin_profile_for_storage(merged)
    stored.pop("_id", None)

    await users_collection.update_one({"_id": ObjectId(family_id)}, {"$set": stored})

    password_email_sent = False
    if password_changed and new_password:
        refreshed = load_nextkin_profile(
            await users_collection.find_one({"_id": ObjectId(family_id)})
        )
        if refreshed:
            await send_family_invite_email(
                family=refreshed,
                owner=owner,
                plain_password=new_password,
                password_only=True,
            )
            password_email_sent = True

    return {
        "message": "Family member updated successfully.",
        "family_id": family_id,
        "updated_fields": list(update_data.keys()),
        "password_email_sent": password_email_sent,
    }


async def delete_family_member(
    family_id: str,
    request: Request,
    authorization: str | None,
):
    decoded = decode_access_token(request, authorization)
    actor = await _resolve_actor(decoded)
    if not actor:
        raise HTTPException(status_code=401, detail="Unauthorized")
    _require_owner_or_family_manager(decoded, actor)
    owner = await _resolve_owner_for_actor(actor)

    family = await users_collection.find_one(
        {
            "_id": ObjectId(family_id),
            "role": "nextkin",
            "owner_id": str(owner["_id"]),
            **FAMILY_ACCESS_MONGO_FILTER,
        }
    )
    if not family:
        raise HTTPException(status_code=404, detail="Family member not found")

    await users_collection.delete_one({"_id": ObjectId(family_id)})
    return {
        "message": f"Family member '{family.get('full_name') or family['email']}' deleted.",
        "deleted_id": family_id,
    }
