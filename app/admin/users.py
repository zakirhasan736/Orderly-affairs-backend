"""Admin user management (metadata only — never vault content)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

import stripe
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.admin.audit import log_admin_action
from app.admin.deps import require_admin, require_system_owner
from app.admin.permissions import (
    user_can_edit_profile_email,
    user_can_force_logout,
    user_can_manage_subscriptions,
    user_can_suspend_accounts,
    user_has_area,
    user_is_read_only,
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


class ReleaseNokAccessBody(BaseModel):
    confirm: bool = False
    note: Optional[str] = Field(default=None, max_length=1000)
    ssdmf_override: bool = False
    certificate_override: bool = False
    wait_override: bool = False
    death_check_override_reason: Optional[str] = Field(default=None, max_length=1000)


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
        "owner_status": user.get("owner_status") or "alive",
        "death_report_pending": bool(user.get("death_report_pending")),
        "authorized_people": user.get("authorized_people") or [],
        "death_verification": _serialize_death_verification(user),
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
    "owner_status": 1,
    "death_report_pending": 1,
    "death_verification": 1,
    "ssdmf_status": 1,
    "death_certificate_uploaded_at": 1,
    "owner_wait_started_at": 1,
    "owner_wait_ends_at": 1,
    "owner_wait_elapsed": 1,
    "owner_wait_reporter_name": 1,
    "death_claim_alert": 1,
}


def _serialize_death_verification(user: dict) -> dict:
    from app.auth.ssdmf import public_death_verification

    return public_death_verification(user)


def _authorized_person_kind(nk: dict) -> str:
    from app.auth.claimant_roles import claimant_kind_label

    return claimant_kind_label(nk)


def _serialize_authorized_person(nk: dict) -> dict:
    from app.auth.claimant_roles import is_attorney_or_executor

    return {
        "id": str(nk["_id"]),
        "full_name": nk.get("full_name") or nk.get("name"),
        "email": nk.get("email"),
        "phone_number": nk.get("phone_number"),
        "relationship": nk.get("relationship"),
        "kind": _authorized_person_kind(nk),
        "access_type": nk.get("access_type") or "nextkin",
        "access_timing": nk.get("access_timing"),
        "immediate_access": bool(nk.get("immediate_access")),
        "portal_role": nk.get("portal_role"),
        "didit_status": nk.get("didit_status"),
        "didit_verified_at": nk.get("didit_verified_at"),
        "is_attorney_or_executor": is_attorney_or_executor(nk),
    }


async def _authorized_people_for_owners(owner_ids: list[str]) -> dict[str, list[dict]]:
    if not owner_ids:
        return {}
    out: dict[str, list[dict]] = {oid: [] for oid in owner_ids}
    cursor = users_collection.find(
        {
            "role": "nextkin",
            "owner_id": {"$in": owner_ids},
            "access_revoked": {"$ne": True},
            "deleted_at": {"$exists": False},
        },
        {
            "email": 1,
            "full_name": 1,
            "name": 1,
            "phone_number": 1,
            "relationship": 1,
            "access_type": 1,
            "access_timing": 1,
            "immediate_access": 1,
            "portal_role": 1,
            "owner_id": 1,
            "didit_status": 1,
            "didit_verified_at": 1,
        },
    )
    async for nk in cursor:
        oid = str(nk.get("owner_id") or "")
        if oid in out:
            out[oid].append(_serialize_authorized_person(nk))
    return out


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
    rows = [u async for u in cursor]
    people_map = await _authorized_people_for_owners(
        [str(u["_id"]) for u in rows]
    )
    for u in rows:
        packed = _serialize_user(u)
        packed["authorized_people"] = people_map.get(str(u["_id"]), [])
        users.append(packed)

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

    people_map = await _authorized_people_for_owners([str(user["_id"])])
    packed = _serialize_user(user, section_count=section_count)
    packed["authorized_people"] = people_map.get(str(user["_id"]), [])
    try:
        from app.auth.after_death_case import (
            admin_case_payload,
            current_certificate,
            enrolled_claimants,
            open_case_for_owner,
        )

        full = await users_collection.find_one({"_id": user["_id"]}) or user
        case = await open_case_for_owner(str(user["_id"]))
        if case:
            packed["after_death_case"] = admin_case_payload(
                case,
                owner=full,
                claimants=await enrolled_claimants(str(user["_id"])),
                cert=await current_certificate(case),
            )
            packed["death_verification"] = _serialize_death_verification(full)
    except Exception as exc:
        print("⚠️ after-death admin payload failed:", exc)
    return packed


@admin_users_router.post("/{user_id}/release-nok-access")
async def release_nok_access(
    user_id: str,
    payload: ReleaseNokAccessBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Human-reviewed vault unlock for named next of kin (claim-link email)."""
    admin = await require_admin(request, authorization)
    if user_is_read_only(admin):
        raise HTTPException(403, "Read-only admin cannot release vault access")
    if not user_has_area(admin, "legacy"):
        raise HTTPException(403, "Legacy access permission required to release next-of-kin vault access")
    if not payload.confirm:
        raise HTTPException(400, "Confirm this release to continue")

    user = await users_collection.find_one({"_id": _oid(user_id), "role": "owner"})
    if not user:
        raise HTTPException(404, "User not found")

    from app.auth.ssdmf import public_death_verification, run_owner_ssdmf
    from app.auth.service import admin_release_nok_vault_access

    info = public_death_verification(user)
    if info.get("certificate_uploaded") and info.get("ssdmf_status") in {
        "NOT_RUN",
        None,
        "",
    }:
        try:
            await run_owner_ssdmf(user, force=True)
            user = await users_collection.find_one({"_id": user["_id"]}) or user
        except Exception as exc:
            print("⚠️ SSDMF before release failed:", exc)

    result = await admin_release_nok_vault_access(
        owner_ref=str(user["_id"]),
        admin_email=str(admin.get("email") or ""),
        admin_id=str(admin.get("_id") or ""),
        note=payload.note,
        ssdmf_override=payload.ssdmf_override,
        certificate_override=payload.certificate_override,
        wait_override=payload.wait_override,
        death_check_override_reason=payload.death_check_override_reason,
    )
    if result.get("reason") == "owner_not_found":
        raise HTTPException(404, "Owner not found")
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)

    await log_admin_action(
        admin.get("email") or "",
        "nok.release_access",
        str(user.get("email") or user_id),
        {
            "upon_death_granted": result.get("upon_death_granted"),
            "already_deceased": result.get("already_deceased"),
            "note": payload.note,
            "ssdmf_override": payload.ssdmf_override,
            "certificate_override": payload.certificate_override,
            "wait_override": payload.wait_override,
        },
    )
    return {
        "success": True,
        "message": (
            f"Released vault access. Claim emails sent to "
            f"{result.get('upon_death_granted') or 0} next of kin "
            "whose identity verification is complete. Others receive a claim "
            "link after they finish ID verification."
        ),
        **result,
    }


@admin_users_router.get("/{user_id}/death-certificate")
async def admin_death_certificate(
    user_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    if not user_has_area(admin, "legacy"):
        raise HTTPException(403, "Legacy access permission required")
    user = await users_collection.find_one({"_id": _oid(user_id), "role": "owner"})
    if not user:
        raise HTTPException(404, "User not found")
    rec = user.get("death_verification") if isinstance(user.get("death_verification"), dict) else {}
    cert = rec.get("certificate") if isinstance(rec.get("certificate"), dict) else {}
    key = str(cert.get("s3_key") or "").strip()
    if not key:
        raise HTTPException(404, "No death certificate on file")
    from app.storage.section_s3 import presign_section_get_url

    url = presign_section_get_url(
        s3_key=key,
        bucket=str(cert.get("s3_bucket") or "").strip() or None,
        expires_in=15 * 60,
    )
    await log_admin_action(
        admin.get("email") or "",
        "nok.view_death_certificate",
        str(user.get("email") or user_id),
    )
    return {
        "filename": cert.get("filename"),
        "uploaded_at": cert.get("uploaded_at"),
        "uploaded_by": cert.get("uploaded_by_name"),
        "url": url,
        "url_expires_in": 15 * 60,
    }


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
    hard: bool = Query(
        default=True,
        description="Hard-purge all owned data (default). soft=false is revoke-only.",
    ),
    reason: str | None = Query(default=None, max_length=500),
):
    """
    Hard-delete owner by default: vault/S3/Cloudinary/Stripe + NOK/family/letters/
    messages/sections, while retaining a hashed identity tombstone for rejoin detection.
    Pass hard=false only to soft-revoke access without wiping data.
    """
    from app.auth.account_purge_service import purge_owner_account

    admin = await require_system_owner(request, authorization)
    if not (reason or "").strip():
        raise HTTPException(400, "reason is required for account deletion")
    user = await users_collection.find_one({"_id": _oid(user_id), "role": "owner"})
    if not user:
        raise HTTPException(404, "User not found")

    if str(user["_id"]) == admin.get("user_id"):
        raise HTTPException(400, "Cannot delete your own admin account")

    now = datetime.utcnow()
    if hard:
        try:
            summary = await purge_owner_account(
                user,
                deleted_by="admin",
                reason=reason,
                deleted_by_email=admin.get("email"),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        await log_admin_action(
            admin.get("email") or "",
            "user_hard_delete",
            target=user_id,
            meta={
                "email": user.get("email"),
                "reason": reason,
                "tombstone": summary.get("tombstone"),
                "mongo": summary.get("mongo"),
                "s3": summary.get("s3"),
                "stripe": summary.get("stripe"),
            },
        )
        return {
            "message": "User and all linked data permanently deleted",
            "id": user_id,
            "summary": summary,
        }

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
        meta={"email": user.get("email"), "reason": reason},
    )
    return {"message": "User soft-deleted", "id": user_id, "deleted_at": now}


class ForceLogoutRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


@admin_users_router.post("/{user_id}/force-logout")
async def force_logout(
    user_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    payload: ForceLogoutRequest = Body(default_factory=ForceLogoutRequest),
):
    admin = await require_admin(request, authorization)
    if not user_can_force_logout(admin):
        raise HTTPException(403, "Not allowed to force logout users")
    user = await users_collection.find_one({"_id": _oid(user_id), "role": "owner"})
    if not user:
        raise HTTPException(404, "User not found")

    if not (payload.reason or "").strip():
        raise HTTPException(400, "reason is required")

    await revoke_all_user_refresh_tokens(str(user["_id"]))
    await log_admin_action(
        admin.get("email") or "",
        "user_force_logout",
        target=str(user["_id"]),
        meta={"reason": payload.reason},
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
    if not user_can_manage_subscriptions(admin):
        raise HTTPException(403, "Not allowed to manage complimentary access")
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
