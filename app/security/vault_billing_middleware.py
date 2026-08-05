"""Block vault APIs when the kit owner's billing is past due / blocked."""

from __future__ import annotations

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.billing.access import enforce_vault_access
from app.database import users_collection
from app.security.cookie_auth import NOK_ACCESS_COOKIE, OWNER_ACCESS_COOKIE
from app.security.jwt_handler import verify_token
from app.security.vault_paths import _SKIP_METHODS, is_vault_api_path


def _verify_cookie(token: str | None) -> dict | None:
    if not token:
        return None
    decoded = verify_token(token)
    return decoded if decoded else None


def _pick_decoded(request: Request) -> dict | None:
    owner_token = request.cookies.get(OWNER_ACCESS_COOKIE)
    nok_token = request.cookies.get(NOK_ACCESS_COOKIE)
    session_kind = (request.headers.get("X-OA-Session-Kind") or "").strip().lower()
    prefer_nok = session_kind in ("family", "nextkin")

    if prefer_nok:
        return _verify_cookie(nok_token) or _verify_cookie(owner_token)
    return _verify_cookie(owner_token) or _verify_cookie(nok_token)


async def resolve_vault_owner_for_billing(request: Request) -> dict | None:
    """Return the kit owner document when a valid portal session is present."""
    decoded = _pick_decoded(request)
    if not decoded:
        return None

    role = decoded.get("role")
    if role == "owner":
        return await users_collection.find_one(
            {"email": decoded.get("sub"), "role": "owner"},
            {"billing": 1, "email": 1, "role": 1},
        )

    if role != "nextkin":
        return None

    sub = decoded.get("sub")
    user = await users_collection.find_one(
        {"email": sub, "role": "nextkin"},
        {"owner_id": 1},
    )
    if not user and sub:
        try:
            user = await users_collection.find_one(
                {"_id": ObjectId(str(sub)), "role": "nextkin"},
                {"owner_id": 1},
            )
        except (InvalidId, TypeError, ValueError):
            user = None

    owner_id = user.get("owner_id") if user else None
    if not owner_id:
        return None

    try:
        owner_oid = ObjectId(str(owner_id))
    except (InvalidId, TypeError, ValueError):
        return None

    return await users_collection.find_one(
        {"_id": owner_oid, "role": "owner"},
        {"billing": 1, "email": 1, "role": 1},
    )



class VaultBillingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in _SKIP_METHODS:
            return await call_next(request)

        path = request.url.path
        if not is_vault_api_path(path):
            return await call_next(request)

        owner = await resolve_vault_owner_for_billing(request)
        if owner:
            try:
                enforce_vault_access(owner)
            except HTTPException as exc:
                from starlette.responses import JSONResponse

                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                )

        return await call_next(request)
