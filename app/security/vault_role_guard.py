"""Central vault role guard — owner / approved nextkin only."""

from __future__ import annotations

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.database import users_collection
from app.security.token_resolver import decode_owner_or_nok_token
from app.security.vault_billing_middleware import _pick_decoded
from app.security.vault_paths import _SKIP_METHODS, is_vault_api_path

_ALLOWED_VAULT_ROLES = frozenset({"owner", "nextkin"})


async def _load_nextkin_actor(decoded: dict) -> dict | None:
    sub = decoded.get("sub")
    if not sub:
        return None
    user = await users_collection.find_one({"email": sub, "role": "nextkin"})
    if user:
        return user
    try:
        return await users_collection.find_one(
            {"_id": ObjectId(str(sub)), "role": "nextkin"}
        )
    except (InvalidId, TypeError, ValueError):
        return None


async def resolve_vault_actor(
    request: Request,
    authorization: str | None = Header(default=None),
) -> tuple[dict, dict]:
    """
    Resolve (actor, owner) for vault routes.

    - Owner: actor == owner
    - Next-of-Kin / family collaborator: actor is portal user, owner is kit owner
    """
    decoded = decode_owner_or_nok_token(request, authorization)
    role = decoded.get("role")

    if role == "owner":
        owner = await users_collection.find_one(
            {"email": decoded["sub"], "role": "owner"}
        )
        if not owner:
            raise HTTPException(status_code=401, detail="Owner not found")
        return owner, owner

    if role != "nextkin":
        raise HTTPException(status_code=403, detail="Invalid role for vault access")

    actor = await _load_nextkin_actor(decoded)
    if not actor:
        raise HTTPException(status_code=401, detail="Collaborator not found")

    if not actor.get("immediate_access", False):
        raise HTTPException(status_code=403, detail="Access not approved")

    owner_id = actor.get("owner_id")
    if not owner_id:
        raise HTTPException(status_code=404, detail="Kit owner not found")

    try:
        owner = await users_collection.find_one(
            {"_id": ObjectId(str(owner_id)), "role": "owner"}
        )
    except (InvalidId, TypeError, ValueError):
        owner = None
    if not owner:
        raise HTTPException(status_code=404, detail="Kit owner not found")

    return actor, owner


class VaultRoleGuardMiddleware(BaseHTTPMiddleware):
    """
    Reject vault API calls from disallowed roles before route handlers run.

    Requires a valid owner or approved next-of-kin / family session cookie.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in _SKIP_METHODS:
            return await call_next(request)

        if not is_vault_api_path(request.url.path):
            return await call_next(request)

        decoded = _pick_decoded(request)
        if not decoded:
            return await call_next(request)

        role = decoded.get("role")
        if role not in _ALLOWED_VAULT_ROLES:
            return JSONResponse(
                status_code=403,
                content={"detail": "This portal role cannot access vault data"},
            )

        if role == "nextkin":
            actor = await _load_nextkin_actor(decoded)
            if not actor:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Collaborator not found"},
                )
            if not actor.get("immediate_access", False):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Access not approved"},
                )

        return await call_next(request)
