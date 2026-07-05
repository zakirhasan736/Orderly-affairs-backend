"""Issue and revoke HttpOnly cookie sessions (access + refresh tokens)."""

from fastapi import Request, Response

from app.config import settings
from app.security.cookie_auth import (
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


async def issue_owner_session(response: Response, user: dict) -> dict:
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

    billing = user.get("billing", {})
    return {
        "authenticated": True,
        "role": "owner",
        "email": user["email"],
        "mfa_required": False,
        "billing_status": billing.get("status", "pending"),
        "requires_billing": billing.get("status") in ["pending", "blocked"],
        "message": "Login successful",
    }


async def issue_nok_session(response: Response, user: dict) -> dict:
    access = create_access_token(user)
    refresh_plain, _ = await create_refresh_token(
        user_id=str(user["_id"]),
        role="nextkin",
        email=user["email"],
    )

    set_auth_cookie(
        response,
        name=NOK_ACCESS_COOKIE,
        value=access,
        max_age_seconds=_access_max_age(),
    )
    set_auth_cookie(
        response,
        name=NOK_REFRESH_COOKIE,
        value=refresh_plain,
        max_age_seconds=_refresh_max_age(),
    )

    return {
        "authenticated": True,
        "role": "nextkin",
        "owner_id": str(user.get("owner_id")),
        "email": user.get("email"),
        "message": "Next-of-Kin login successful",
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

    clear_auth_cookies(response, owner=True, nok=False)
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

    clear_auth_cookies(response, owner=False, nok=True)
    return {"message": "Next-of-Kin logged out"}


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

    refresh_cookie = (
        OWNER_REFRESH_COOKIE if role == "owner" else NOK_REFRESH_COOKIE
    )
    access_cookie = OWNER_ACCESS_COOKIE if role == "owner" else NOK_ACCESS_COOKIE

    plain = request.cookies.get(refresh_cookie)
    if not plain:
        raise ValueError("missing_refresh")

    rotated = await rotate_refresh_token(plain)
    if not rotated:
        clear_auth_cookies(
            response,
            owner=role == "owner",
            nok=role == "nextkin",
        )
        raise ValueError("invalid_refresh")

    new_plain, meta = rotated
    user = await resolve_user_from_id(meta["user_id"], meta["role"])
    if not user:
        raise ValueError("user_not_found")

    access = create_access_token(user)
    set_auth_cookie(
        response,
        name=access_cookie,
        value=access,
        max_age_seconds=_access_max_age(),
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
