"""
Official vault security model — three principals, RBAC + ABAC enforcement.

Principals:
  - owner  — full kit control
  - family — owner-dashboard collaborator (portal RBAC + area ABAC)
  - nok    — survivor portal (section ABAC, read-only writes)

See docs/SECURITY_MODEL.md for the client-facing specification.
"""

from __future__ import annotations

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.auth.access_types import (
    ACCESS_TYPE_FAMILY,
    ACCESS_TYPE_NEXTKIN,
    is_family_collaborator,
    is_nextkin_collaborator,
)
from app.auth.family_access import family_has_dashboard_area
from app.auth.portal_roles import resolve_dashboard_permissions
from app.database import users_collection
from app.security.access_control import assert_section_read_access
from app.security.token_resolver import decode_access_token, decode_owner_or_nok_token
from app.security.vault_billing_middleware import _pick_decoded
from app.security.vault_paths import (
    _SKIP_METHODS,
    is_nok_only_api_path,
    is_vault_api_path,
)

PRINCIPAL_OWNER = "owner"
PRINCIPAL_FAMILY = "family"
PRINCIPAL_NOK = "nok"


def resolve_principal(user: dict | None) -> str:
    """Map a loaded user document to owner | family | nok."""
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    role = user.get("role")
    if role == "owner":
        return PRINCIPAL_OWNER
    if role != "nextkin":
        raise HTTPException(status_code=403, detail="Invalid role for vault access")
    if is_family_collaborator(user):
        return PRINCIPAL_FAMILY
    return PRINCIPAL_NOK


async def load_collaborator_from_decoded(decoded: dict) -> dict | None:
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


def require_nok_principal(user: dict, *, detail: str = "Next-of-Kin portal access required") -> None:
    if resolve_principal(user) != PRINCIPAL_NOK:
        raise HTTPException(status_code=403, detail=detail)


def require_family_or_owner_principal(
    user: dict,
    *,
    detail: str = "Owner or family collaborator access required",
) -> None:
    principal = resolve_principal(user)
    if principal not in (PRINCIPAL_OWNER, PRINCIPAL_FAMILY):
        raise HTTPException(status_code=403, detail=detail)


def require_owner_principal(user: dict, *, detail: str = "Owner access required") -> None:
    if resolve_principal(user) != PRINCIPAL_OWNER:
        raise HTTPException(status_code=403, detail=detail)


def assert_abac_vault_section_read(user: dict, section_id: str) -> None:
    """
    ABAC — attribute-based section read (owner, family, NOK).

    Delegates to assert_section_read_access which enforces grants + NOK hidden sections.
    """
    assert_section_read_access(user, section_id)


def assert_abac_family_dashboard_area(user: dict, area_id: str) -> None:
    """ABAC — family collaborator dashboard area grant."""
    if resolve_principal(user) == PRINCIPAL_OWNER:
        return
    if not is_family_collaborator(user):
        raise HTTPException(status_code=403, detail="Family collaborator access required")
    if not user.get("immediate_access", False) or user.get("access_revoked"):
        raise HTTPException(status_code=403, detail="Access not approved")
    if not family_has_dashboard_area(user, area_id):
        raise HTTPException(
            status_code=403,
            detail=f"No access to dashboard area {area_id}",
        )


def assert_rbac_family_permission(user: dict, permission: str) -> None:
    """RBAC — family portal role capability (can_write, can_manage_nextkin, …)."""
    if resolve_principal(user) == PRINCIPAL_OWNER:
        return
    if not is_family_collaborator(user):
        raise HTTPException(status_code=403, detail="Family collaborator access required")
    perms = resolve_dashboard_permissions(user)
    if not perms.get(permission):
        raise HTTPException(
            status_code=403,
            detail="Your family role does not allow this action",
        )


async def resolve_vault_actor_with_principal(
    request: Request,
    authorization: str | None = Header(default=None),
) -> tuple[dict, dict, str]:
    """Return (actor, owner, principal) for vault routes."""
    decoded = decode_owner_or_nok_token(request, authorization)
    role = decoded.get("role")

    if role == "owner":
        owner = await users_collection.find_one(
            {"email": decoded["sub"], "role": "owner"}
        )
        if not owner:
            raise HTTPException(status_code=401, detail="Owner not found")
        return owner, owner, PRINCIPAL_OWNER

    if role != "nextkin":
        raise HTTPException(status_code=403, detail="Invalid role for vault access")

    actor = await load_collaborator_from_decoded(decoded)
    if not actor:
        raise HTTPException(status_code=401, detail="Collaborator not found")
    if not actor.get("immediate_access", False) or actor.get("access_revoked"):
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

    return actor, owner, resolve_principal(actor)


class VaultPrincipalMiddleware(BaseHTTPMiddleware):
    """
    Enforce NOK-only API paths for true Next-of-Kin principals.

    Family collaborators use the owner dashboard — they must not call NOK portal APIs.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in _SKIP_METHODS:
            return await call_next(request)

        path = request.url.path
        if not is_nok_only_api_path(path, request.method):
            return await call_next(request)

        decoded = _pick_decoded(request)
        if not decoded:
            return await call_next(request)

        if decoded.get("role") == "owner":
            return JSONResponse(
                status_code=403,
                content={"detail": "Owner session cannot use Next-of-Kin portal APIs"},
            )

        actor = await load_collaborator_from_decoded(decoded)
        if not actor:
            return JSONResponse(
                status_code=401,
                content={"detail": "Collaborator not found"},
            )

        if not is_nextkin_collaborator(actor):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "Family collaborators use the owner dashboard. "
                        "Next-of-Kin portal APIs are restricted to Next-of-Kin accounts."
                    )
                },
            )

        if not actor.get("immediate_access", False) or actor.get("access_revoked"):
            return JSONResponse(
                status_code=403,
                content={"detail": "Access not approved"},
            )

        return await call_next(request)
