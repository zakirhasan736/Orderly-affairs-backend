"""Admin user management (metadata only — never vault content)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

import stripe
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.admin.audit import log_admin_action
from app.admin.deps import require_admin, require_system_owner
from app.admin.permissions import (
    user_can_clear_rate_limits,
    user_can_edit_profile_email,
    user_can_force_logout,
    user_can_suspend_accounts,
)
from app.billing.access import compute_comp_end, default_billing_fields, get_comp
from app.config import settings
from app.database import section_data_collection, users_collection
from app.notifications.comp_emails import CompEmailEvent, send_comp_email
from app.security.refresh_tokens import revoke_all_user_refresh_tokens

stripe.api_key = settings.STRIPE_SECRET_KEY

admin_users_router = APIRouter(prefix="/admin/users", tags=["admin-users"])


class PatchUserRequest(BaseModel):
    suspend: Optional[bool] = None
    full_name: Optional[str] = Field(default=None, max_length=200)
    name: Optional[str] = Field(default=None, max_length=200)
    is_admin: Optional[bool] = None
    role_admin: Optional[bool] = None
    admin_role: Optional[Literal[
        "super_admin",
        "system_owner",
        "admin",
        "editor",
        "viewer",
        "support",
    ]] = None
    email: Optional[str] = Field(default=None, max_length=320)
    reason: Optional[str] = Field(default=None, max_length=1000)


class GrantCompBody(BaseModel):
    kind: Literal["lifetime", "duration"]
    duration_days: Optional[int] = None
    duration_months: Optional[int] = None
    duration_years: Optional[int] = None
    note: Optional[str] = None
    send_email: bool = True
    cancel_stripe_subscription: bool = True


def _oid(user_id: str) -> ObjectId:
    try:
        return ObjectId(user_id)
    except (InvalidId, TypeError):
        raise HTTPException(400, "Invalid user id")


def _serialize_user(user: dict, *, section_count: int | None = None) -> dict:
    billing = user.get("billing") or {}
    comp = get_comp(billing)
    return {
        "id": str(user["_id"]),
        "email": user.get("email"),
        "full_name": user.get("full_name") or user.get("name"),
        "role": user.get("role"),
        "billing_status": billing.get("status"),
        "plan": billing.get("plan"),
        "trial_end": billing.get("trial_end"),
        "is_complimentary": bool(
            comp["enabled"]
            and (
                comp["kind"] == "lifetime"
                or (comp["ends_at"] and comp["ends_at"] > datetime.utcnow())
            )
        ),
        "comp_kind": comp.get("kind"),
        "comp_ends_at": comp.get("ends_at"),
        "last_login": user.get("last_login") or user.get("owner_last_login"),
        "suspended": bool(user.get("suspended")),
        "deleted_at": user.get("deleted_at"),
        "access_revoked": bool(user.get("access_revoked")),
        "created_at": user.get("created_at"),
        "is_admin": bool(user.get("is_admin")),
        "role_admin": bool(user.get("role_admin")),
        "admin_role": user.get("admin_role"),
        "admin_mfa_enabled": bool(user.get("admin_mfa_enabled")),
        "section_count": section_count,
    }


METADATA_PROJECTION = {
    "email": 1,
    "full_name": 1,
    "name": 1,
    "role": 1,
    "billing": 1,
    "last_login": 1,
    "owner_last_login": 1,
    "suspended": 1,
    "deleted_at": 1,
    "access_revoked": 1,
    "created_at": 1,
    "is_admin": 1,
    "role_admin": 1,
    "admin_role": 1,
    "admin_mfa_enabled": 1,
}


@admin_users_router.get("/")
async def list_users(
    request: Request,
    authorization: str | None = Header(default=None),
    q: Optional[str] = Query(default=None, max_length=200),
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    admin = await require_admin(request, authorization)

    query: dict = {"role": "owner", "deleted_at": {"$exists": False}}
    # Also include soft-deleted when filtering suspended
    if status == "suspended":
        query = {"role": "owner", "suspended": True}
    elif status == "deleted":
        query = {"role": "owner", "deleted_at": {"$exists": True, "$ne": None}}
    elif status == "active":
        query["suspended"] = {"$ne": True}
        query["billing.status"] = {"$in": ["active", "complimentary"]}
    elif status == "trial":
        query["billing.status"] = "trialing"
    elif status:
        query["billing.status"] = status

    if q:
        q_clean = q.strip()
        query["$or"] = [
            {"email": {"$regex": q_clean, "$options": "i"}},
            {"full_name": {"$regex": q_clean, "$options": "i"}},
            {"name": {"$regex": q_clean, "$options": "i"}},
        ]

    total = await users_collection.count_documents(query)
    skip = (page - 1) * page_size
    cursor = (
        users_collection.find(query, METADATA_PROJECTION)
        .sort("created_at", -1)
        .skip(skip)
        .limit(page_size)
    )

    users = []
    async for u in cursor:
        users.append(_serialize_user(u))

    await log_admin_action(
        admin.get("email") or "",
        "users_list",
        meta={"q": q, "status": status, "page": page},
    )
    return {
        "users": users,
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@admin_users_router.get("/{user_id}")
async def get_user(
    user_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    await require_admin(request, authorization)
    user = await users_collection.find_one(
        {"_id": _oid(user_id), "role": "owner"},
        METADATA_PROJECTION,
    )
    if not user:
        raise HTTPException(404, "User not found")

    section_count = await section_data_collection.count_documents(
        {"owner_id": str(user["_id"])}
    )
    # Some docs may store ObjectId
    if section_count == 0:
        section_count = await section_data_collection.count_documents(
            {"owner_id": user["_id"]}
        )

    return _serialize_user(user, section_count=section_count)


@admin_users_router.patch("/{user_id}")
async def patch_user(
    user_id: str,
    payload: PatchUserRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    user = await users_collection.find_one({"_id": _oid(user_id), "role": "owner"})
    if not user:
        raise HTTPException(404, "User not found")

    updates: dict = {"updated_at": datetime.utcnow()}

    if payload.suspend is not None:
        if not user_can_suspend_accounts(admin):
            raise HTTPException(403, "Not allowed to suspend or reinstate accounts")
        updates["suspended"] = payload.suspend
        if payload.suspend:
            updates["access_revoked"] = True
        else:
            updates["access_revoked"] = False

    name = payload.full_name if payload.full_name is not None else payload.name
    if name is not None:
        if not user_can_edit_profile_email(admin):
            raise HTTPException(403, "Not allowed to edit user profile")
        updates["full_name"] = name.strip()
        updates["name"] = name.strip()

    if payload.email is not None:
        if not user_can_edit_profile_email(admin):
            raise HTTPException(403, "Not allowed to change user email")
        new_email = payload.email.lower().strip()
        if new_email and new_email != (user.get("email") or "").lower():
            clash = await users_collection.find_one({"email": new_email})
            if clash:
                raise HTTPException(400, "Email already in use")
            updates["email"] = new_email
            updates["email_previous"] = user.get("email")

    elevating = (
        payload.is_admin is not None
        or payload.role_admin is not None
        or payload.admin_role is not None
    )
    if elevating:
        if admin.get("admin_role") not in ("system_owner", "super_admin"):
            raise HTTPException(403, "System owner only")
        if payload.is_admin is not None:
            updates["is_admin"] = payload.is_admin
        if payload.role_admin is not None:
            updates["role_admin"] = payload.role_admin
        if payload.admin_role is not None:
            updates["admin_role"] = payload.admin_role

    await users_collection.update_one({"_id": user["_id"]}, {"$set": updates})
    await log_admin_action(
        admin.get("email") or "",
        "user_patch",
        target=str(user["_id"]),
        meta={
            "updates": {k: v for k, v in updates.items() if k != "updated_at"},
            "reason": payload.reason,
        },
    )

    updated = await users_collection.find_one(
        {"_id": user["_id"]}, METADATA_PROJECTION
    )
    return _serialize_user(updated or user)


@admin_users_router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    hard: bool = Query(default=False),
):
    admin = await require_system_owner(request, authorization)
    user = await users_collection.find_one({"_id": _oid(user_id), "role": "owner"})
    if not user:
        raise HTTPException(404, "User not found")

    if str(user["_id"]) == admin.get("user_id"):
        raise HTTPException(400, "Cannot delete your own admin account")

    now = datetime.utcnow()
    if hard:
        await revoke_all_user_refresh_tokens(str(user["_id"]))
        await users_collection.delete_one({"_id": user["_id"]})
        await log_admin_action(
            admin.get("email") or "",
            "user_hard_delete",
            target=str(user["_id"]),
            meta={"email": user.get("email")},
        )
        return {"message": "User permanently deleted", "id": user_id}

    await users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "deleted_at": now,
                "access_revoked": True,
                "suspended": True,
                "updated_at": now,
            }
        },
    )
    await revoke_all_user_refresh_tokens(str(user["_id"]))
    await log_admin_action(
        admin.get("email") or "",
        "user_soft_delete",
        target=str(user["_id"]),
        meta={"email": user.get("email")},
    )
    return {"message": "User soft-deleted", "id": user_id, "deleted_at": now}


@admin_users_router.post("/{user_id}/force-logout")
async def force_logout(
    user_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    if not user_can_force_logout(admin):
        raise HTTPException(403, "Not allowed to force logout users")
    user = await users_collection.find_one({"_id": _oid(user_id), "role": "owner"})
    if not user:
        raise HTTPException(404, "User not found")

    await revoke_all_user_refresh_tokens(str(user["_id"]))
    await log_admin_action(
        admin.get("email") or "",
        "user_force_logout",
        target=str(user["_id"]),
    )
    return {"message": "All refresh tokens revoked", "id": user_id}


@admin_users_router.post("/{user_id}/grant-comp")
async def grant_comp_for_user(
    user_id: str,
    payload: GrantCompBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    user = await users_collection.find_one({"_id": _oid(user_id), "role": "owner"})
    if not user:
        raise HTTPException(404, "User not found")

    result = await apply_complimentary_grant(
        user=user,
        kind=payload.kind,
        duration_days=payload.duration_days,
        duration_months=payload.duration_months,
        duration_years=payload.duration_years,
        note=payload.note,
        send_email=payload.send_email,
        cancel_stripe_subscription=payload.cancel_stripe_subscription,
        granted_by=admin.get("email") or admin.get("sub"),
    )
    await log_admin_action(
        admin.get("email") or "",
        "user_grant_comp",
        target=str(user["_id"]),
        meta={"kind": payload.kind, "note": payload.note},
    )
    return result


async def apply_complimentary_grant(
    *,
    user: dict,
    kind: str,
    duration_days: int | None = None,
    duration_months: int | None = None,
    duration_years: int | None = None,
    note: str | None = None,
    send_email: bool = True,
    cancel_stripe_subscription: bool = True,
    granted_by: str | None = None,
) -> dict:
    now = datetime.utcnow()
    email = user["email"].lower().strip()
    ends_at = compute_comp_end(
        kind=kind,
        duration_days=duration_days,
        duration_months=duration_months,
        duration_years=duration_years,
        starts_at=now,
    )

    billing = user.get("billing") or default_billing_fields()
    sub_id = billing.get("subscription_id")

    if cancel_stripe_subscription and sub_id:
        try:
            stripe.Subscription.delete(sub_id)
        except Exception as exc:
            print(f"grant-comp: could not cancel Stripe sub {sub_id}: {exc}")

    update = {
        "billing.status": "complimentary",
        "billing.is_trial": False,
        "billing.plan": "complimentary",
        "billing.subscription_id": None if cancel_stripe_subscription else sub_id,
        "billing.lock_reason": None,
        "billing.locked_at": None,
        "billing.comp.enabled": True,
        "billing.comp.kind": kind,
        "billing.comp.starts_at": now,
        "billing.comp.ends_at": ends_at,
        "billing.comp.granted_by": granted_by,
        "billing.comp.granted_at": now,
        "billing.comp.note": note,
        "billing.comp.reminders_sent": [],
        "updated_at": now,
    }

    await users_collection.update_one({"_id": user["_id"]}, {"$set": update})

    if send_email:
        updated = await users_collection.find_one({"_id": user["_id"]})
        try:
            await send_comp_email(
                user=updated or user,
                event=CompEmailEvent.GRANTED,
                ends_at=ends_at,
            )
        except Exception as exc:
            print(f"grant-comp email failed for {email}: {exc}")

    return {
        "message": "Complimentary access granted",
        "email": email,
        "kind": kind,
        "ends_at": ends_at,
        "status": "complimentary",
    }
