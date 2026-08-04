"""Shared admin authorization helpers for the system-owner panel."""

from __future__ import annotations

from typing import Any

from fastapi import Header, HTTPException, Request

from app.admin.permissions import (
    normalize_admin_role,
    resolve_areas_for_role,
    user_can_clear_rate_limits,
    user_can_delete_users,
    user_can_edit_profile_email,
    user_can_force_logout,
    user_can_issue_coupons,
    user_can_manage_roles,
    user_can_manage_subscriptions,
    user_can_suspend_accounts,
    user_has_area,
    user_is_read_only,
)
from app.config import settings
from app.database import users_collection
from app.security.cookie_auth import ADMIN_ACCESS_COOKIE, OWNER_ACCESS_COOKIE
from app.security.jwt_handler import verify_token
from app.security.token_resolver import decode_access_token, decode_admin_token


def parse_admin_emails() -> set[str]:
    raw = (settings.ADMIN_EMAILS or "").strip()
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def is_permitted_admin(user: dict | None) -> bool:
    if not user or user.get("role") != "owner":
        return False
    if user.get("deleted_at") or user.get("access_revoked") is True:
        return False
    if user.get("suspended") is True:
        return False

    email = str(user.get("email") or "").strip().lower()
    if email and email in parse_admin_emails():
        return True
    if user.get("is_admin") is True:
        return True
    if user.get("role_admin") is True:
        return True
    return False


def resolve_admin_role(user: dict) -> str:
    if user.get("admin_role"):
        return normalize_admin_role(user.get("admin_role"))

    email = str(user.get("email") or "").strip().lower()
    if email and email in parse_admin_emails():
        return "super_admin"
    if user.get("is_admin") or user.get("role_admin"):
        return "admin"
    return "viewer"


def admin_payload_from_user(user: dict, decoded: dict | None = None) -> dict[str, Any]:
    email = str(user.get("email") or "").strip().lower()
    role = resolve_admin_role(user)
    areas = resolve_areas_for_role(
        role,
        user.get("admin_areas") if isinstance(user.get("admin_areas"), list) else None,
    )
    base = dict(decoded or {})
    return {
        **base,
        "role": "admin",
        "email": email,
        "sub": email,
        "admin_role": role,
        "admin_areas": areas,
        "can_manage_roles": user_can_manage_roles({**user, "admin_role": role}),
        "can_edit_profile_email": user_can_edit_profile_email({**user, "admin_role": role}),
        "can_suspend_accounts": user_can_suspend_accounts({**user, "admin_role": role}),
        "can_clear_rate_limits": user_can_clear_rate_limits({**user, "admin_role": role}),
        "can_force_logout": user_can_force_logout({**user, "admin_role": role}),
        "can_delete_users": user_can_delete_users({**user, "admin_role": role}),
        "can_manage_subscriptions": user_can_manage_subscriptions({**user, "admin_role": role}),
        "can_issue_coupons": user_can_issue_coupons({**user, "admin_role": role}),
        "read_only": user_is_read_only({**user, "admin_role": role}),
        "user_id": str(user["_id"]),
        "user": user,
    }


async def require_admin(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    """
    Prefer isolated admin cookie (JWT role=admin).

    Owner-cookie fallback is disabled outside development unless
    ADMIN_ALLOW_OWNER_COOKIE_FALLBACK=true.
    """
    admin_cookie = request.cookies.get(ADMIN_ACCESS_COOKIE)
    if admin_cookie:
        decoded = verify_token(admin_cookie)
        if decoded and decoded.get("role") == "admin":
            email = str(decoded.get("sub") or decoded.get("email") or "").strip().lower()
            user = await users_collection.find_one({"email": email, "role": "owner"})
            if not user or not is_permitted_admin(user):
                raise HTTPException(403, "Admin only")
            return admin_payload_from_user(user, decoded)

    try:
        decoded = decode_admin_token(request, authorization)
        if decoded.get("role") == "admin":
            email = str(decoded.get("sub") or decoded.get("email") or "").strip().lower()
            user = await users_collection.find_one({"email": email, "role": "owner"})
            if user and is_permitted_admin(user):
                return admin_payload_from_user(user, decoded)
    except HTTPException:
        pass

    if settings.allow_owner_cookie_admin_fallback:
        owner_cookie = request.cookies.get(OWNER_ACCESS_COOKIE)
        if owner_cookie or authorization:
            try:
                decoded = decode_access_token(
                    request,
                    authorization,
                    access_cookie=OWNER_ACCESS_COOKIE,
                )
            except HTTPException:
                raise HTTPException(403, "Admin only")

            if decoded.get("role") in ("admin", "owner"):
                email = str(decoded.get("sub") or decoded.get("email") or "").strip().lower()
                user = await users_collection.find_one({"email": email, "role": "owner"})
                if user and is_permitted_admin(user):
                    return admin_payload_from_user(user, decoded)

    raise HTTPException(403, "Admin only")


async def require_system_owner(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    admin = await require_admin(request, authorization)
    role = normalize_admin_role(admin.get("admin_role"))
    if role not in ("super_admin", "system_owner"):
        raise HTTPException(403, "Super Admin only")
    return admin


async def require_admin_area(
    request: Request,
    area: str,
    authorization: str | None = Header(default=None),
) -> dict:
    admin = await require_admin(request, authorization)
    user = admin.get("user") or {}
    if not user_has_area({**user, "admin_role": admin.get("admin_role"), "admin_areas": admin.get("admin_areas")}, area):
        raise HTTPException(403, f"No access to area: {area}")
    return admin
