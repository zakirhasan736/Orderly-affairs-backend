"""Issue and revoke HttpOnly cookie sessions (access + refresh tokens)."""

from datetime import timedelta

from fastapi import Request, Response

from app.config import settings
from app.security.cookie_auth import (
    ADMIN_ACCESS_COOKIE,
    ADMIN_REFRESH_COOKIE,
    NOK_ACCESS_COOKIE,
    NOK_REFRESH_COOKIE,
    OWNER_ACCESS_COOKIE,
    OWNER_REFRESH_COOKIE,
    clear_auth_cookies,
    set_auth_cookie,
)
from app.security.jwt_handler import create_access_token
from app.security.refresh_tokens import (
    create_refresh_token,
    revoke_all_user_refresh_tokens,
    revoke_refresh_token,
)


def _access_max_age() -> int:
    return settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


def _refresh_max_age() -> int:
    return settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60


def is_full_kit_access(user: dict) -> bool:
    """True for Full Kit NOK or Full Dashboard family collaborators."""
    level = str(user.get("access_level") or "").strip()
    if level in ("Area-Specific Access", "Section-Specific Access"):
        return False
    if level in (
        "Full Kit Access",
        "Full Dashboard Access",
        "full",
        "full_kit",
        "full_dashboard",
        "",
    ):
        return True
    sections = user.get("authorized_sections")
    return sections == "all" or sections == ["all"]


def _nok_access_minutes(user: dict) -> int:
    if is_full_kit_access(user):
        return max(1, int(settings.NOK_FULL_KIT_ACCESS_TOKEN_EXPIRE_MINUTES))
    return max(1, int(settings.NOK_ACCESS_TOKEN_EXPIRE_MINUTES))


def _nok_access_max_age(user: dict) -> int:
    return _nok_access_minutes(user) * 60


async def issue_owner_session(response: Response, user: dict) -> dict:
    # Owner and family/NOK sessions must never share cookies.
    clear_auth_cookies(response, owner=False, nok=True, admin=True)

    access = create_access_token(user)
    refresh_plain, _ = await create_refresh_token(
        user_id=str(user["_id"]),
        role="owner",
        email=user["email"],
    )

    set_auth_cookie(
        response,
        name=OWNER_ACCESS_COOKIE,
        value=access,
        max_age_seconds=_access_max_age(),
    )
    set_auth_cookie(
        response,
        name=OWNER_REFRESH_COOKIE,
        value=refresh_plain,
        max_age_seconds=_refresh_max_age(),
    )

    from app.billing.access import billing_session_flags

    flags = billing_session_flags(user.get("billing", {}))
    return {
        "authenticated": True,
        "role": "owner",
        "email": user["email"],
        "mfa_required": False,
        "billing_status": flags["billing_status"],
        "requires_billing": flags["requires_billing"],
        "billing_only": flags["billing_only"],
        "is_complimentary": flags["is_complimentary"],
        "comp_ends_at": flags["comp_ends_at"],
        "auto_renew": flags["auto_renew"],
        "trial_mode": flags["trial_mode"],
        "lock_message": flags["lock_message"],
        "message": "Login successful",
    }


async def issue_nok_session(response: Response, user: dict) -> dict:
    # Family/NOK login clears owner + admin cookies so sessions stay isolated.
    clear_auth_cookies(response, owner=True, nok=False, admin=True)

    access_minutes = _nok_access_minutes(user)
    access = create_access_token(
        user,
        expires_delta=timedelta(minutes=access_minutes),
    )
    refresh_plain, _ = await create_refresh_token(
        user_id=str(user["_id"]),
        role="nextkin",
        email=user["email"],
    )

    set_auth_cookie(
        response,
        name=NOK_ACCESS_COOKIE,
        value=access,
        max_age_seconds=_nok_access_max_age(user),
    )
    set_auth_cookie(
        response,
        name=NOK_REFRESH_COOKIE,
        value=refresh_plain,
        max_age_seconds=_refresh_max_age(),
    )

    from app.auth.access_types import resolve_access_type
    from app.auth.portal_roles import resolve_dashboard_permissions, role_label

    access_type = resolve_access_type(user)
    return {
        "authenticated": True,
        "role": "nextkin",
        "access_type": access_type,
        "portal_role": user.get("portal_role") if access_type == "family" else None,
        "portal_role_label": (
            role_label(user.get("portal_role")) if access_type == "family" else None
        ),
        "dashboard_permissions": (
            resolve_dashboard_permissions(user) if access_type == "family" else None
        ),
        "authorized_sections": (
            user.get("authorized_sections") or [] if access_type == "family" else None
        ),
        "access_level": user.get("access_level") if access_type == "family" else None,
        "full_name": user.get("full_name"),
        "owner_id": str(user.get("owner_id")),
        "email": user.get("email"),
        "access_ttl_minutes": access_minutes,
        "message": (
            "Family collaborator login successful"
            if access_type == "family"
            else "Next-of-Kin login successful"
        ),
    }


async def issue_admin_session(response: Response, user: dict) -> dict:
    """Isolated admin panel session (JWT role=admin; DB role stays owner)."""
    clear_auth_cookies(response, owner=True, nok=True, admin=False)

    from app.admin.deps import resolve_admin_role
    from app.admin.permissions import resolve_areas_for_role

    admin_role = resolve_admin_role(user)
    token_user = {**user, "admin_role": admin_role}
    access = create_access_token(token_user, role="admin")
    refresh_plain, _ = await create_refresh_token(
        user_id=str(user["_id"]),
        role="admin",
        email=user["email"],
    )

    set_auth_cookie(
        response,
        name=ADMIN_ACCESS_COOKIE,
        value=access,
        max_age_seconds=_access_max_age(),
    )
    set_auth_cookie(
        response,
        name=ADMIN_REFRESH_COOKIE,
        value=refresh_plain,
        max_age_seconds=_refresh_max_age(),
    )

    return {
        "authenticated": True,
        "role": "admin",
        "admin_role": admin_role,
        "admin_areas": resolve_areas_for_role(
            admin_role,
            user.get("admin_areas")
            if isinstance(user.get("admin_areas"), list)
            else None,
        ),
        "email": user["email"],
        "full_name": user.get("full_name") or user.get("name"),
        "mfa_required": False,
        "message": "Admin login successful",
    }


async def logout_owner_session(
    response: Response,
    request: Request,
) -> dict:
    refresh = request.cookies.get(OWNER_REFRESH_COOKIE)
    if refresh:
        await revoke_refresh_token(refresh)

    access = request.cookies.get(OWNER_ACCESS_COOKIE)
    if access:
        from app.security.jwt_handler import verify_token

        decoded = verify_token(access) or {}
        sub = decoded.get("sub")
        if sub and decoded.get("role") == "owner":
            user = await _find_owner_by_sub(sub)
            if user:
                await revoke_all_user_refresh_tokens(
                    str(user["_id"]),
                    role="owner",
                )

    clear_auth_cookies(response, owner=True, nok=False, admin=False)
    return {"message": "Owner logged out"}


async def logout_nok_session(
    response: Response,
    request: Request,
) -> dict:
    refresh = request.cookies.get(NOK_REFRESH_COOKIE)
    if refresh:
        await revoke_refresh_token(refresh)

    access = request.cookies.get(NOK_ACCESS_COOKIE)
    if access:
        from app.security.jwt_handler import verify_token
        from bson import ObjectId
        from bson.errors import InvalidId

        decoded = verify_token(access) or {}
        if decoded.get("role") == "nextkin":
            try:
                user_id = str(ObjectId(decoded["sub"]))
                await revoke_all_user_refresh_tokens(user_id, role="nextkin")
            except (InvalidId, KeyError, TypeError):
                pass

    clear_auth_cookies(response, owner=False, nok=True, admin=False)
    return {"message": "Next-of-Kin logged out"}


async def logout_admin_session(
    response: Response,
    request: Request,
) -> dict:
    refresh = request.cookies.get(ADMIN_REFRESH_COOKIE)
    if refresh:
        await revoke_refresh_token(refresh)

    access = request.cookies.get(ADMIN_ACCESS_COOKIE)
    if access:
        from app.security.jwt_handler import verify_token

        decoded = verify_token(access) or {}
        sub = decoded.get("sub")
        if sub and decoded.get("role") == "admin":
            user = await _find_owner_by_sub(sub)
            if user:
                await revoke_all_user_refresh_tokens(
                    str(user["_id"]),
                    role="admin",
                )

    clear_auth_cookies(response, owner=False, nok=False, admin=True)
    return {"message": "Admin logged out"}


async def _find_owner_by_sub(sub: str):
    from app.database import users_collection

    return await users_collection.find_one({"email": sub, "role": "owner"})


async def refresh_session_from_cookie(
    response: Response,
    request: Request,
    *,
    role: str,
) -> dict:
    from app.security.refresh_tokens import resolve_user_from_id, rotate_refresh_token

    if role == "admin":
        refresh_cookie = ADMIN_REFRESH_COOKIE
        access_cookie = ADMIN_ACCESS_COOKIE
    elif role == "owner":
        refresh_cookie = OWNER_REFRESH_COOKIE
        access_cookie = OWNER_ACCESS_COOKIE
    else:
        refresh_cookie = NOK_REFRESH_COOKIE
        access_cookie = NOK_ACCESS_COOKIE

    plain = request.cookies.get(refresh_cookie)
    if not plain:
        raise ValueError("missing_refresh")

    rotated = await rotate_refresh_token(plain)
    if not rotated:
        clear_auth_cookies(
            response,
            owner=role == "owner",
            nok=role == "nextkin",
            admin=role == "admin",
        )
        raise ValueError("invalid_refresh")

    new_plain, meta = rotated
    user = await resolve_user_from_id(meta["user_id"], meta["role"])
    if not user:
        raise ValueError("user_not_found")

    if role == "admin":
        from app.admin.deps import resolve_admin_role

        admin_role = resolve_admin_role(user)
        access = create_access_token(
            {**user, "admin_role": admin_role},
            role="admin",
        )
        access_max_age = _access_max_age()
    elif role == "nextkin":
        access_minutes = _nok_access_minutes(user)
        access = create_access_token(
            user,
            expires_delta=timedelta(minutes=access_minutes),
        )
        access_max_age = access_minutes * 60
    else:
        access = create_access_token(user)
        access_max_age = _access_max_age()

    set_auth_cookie(
        response,
        name=access_cookie,
        value=access,
        max_age_seconds=access_max_age,
    )
    set_auth_cookie(
        response,
        name=refresh_cookie,
        value=new_plain,
        max_age_seconds=_refresh_max_age(),
    )

    return {
        "authenticated": True,
        "role": meta["role"],
        "email": user.get("email"),
        "message": "Token refreshed",
    }
