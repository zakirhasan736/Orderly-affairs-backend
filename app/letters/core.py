# app/letters/core.py
from fastapi import HTTPException, Request
from typing import Dict, Any, Tuple
from app.database import users_collection
from app.security.cookie_auth import NOK_ACCESS_COOKIE
from app.security.token_resolver import decode_access_token, decode_owner_or_nok_token

async def require_owner(request: Request, authorization: str | None = None) -> Dict[str, Any]:
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner token required")
    owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    return owner


async def require_owner_or_family_letter_access(
    request: Request,
    authorization: str | None = None,
    *,
    write: bool = False,
) -> Dict[str, Any]:
    """Owner always; family needs section 3 (letters) grant. Write needs can_write."""
    decoded = decode_owner_or_nok_token(request, authorization)
    if decoded.get("role") == "owner":
        return await require_owner(request, authorization)

    from app.auth.vault_actor import require_owner_or_family

    kwargs: dict = {
        "area_id": "3",
        "detail": "No access to Next-of-Kin letters",
    }
    if write:
        kwargs["perm"] = "can_write"
    _, owner = await require_owner_or_family(decoded, **kwargs)
    return owner

async def require_nok(request: Request, authorization: str | None = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    decoded = decode_access_token(request, authorization, access_cookie=NOK_ACCESS_COOKIE)
    if decoded.get("role") != "nextkin":
        raise HTTPException(status_code=403, detail="Next-of-Kin token required")

    nk = await users_collection.find_one({"email": decoded["email"], "role": "nextkin"})
    if not nk:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found")
    if not nk.get("immediate_access", False):
        raise HTTPException(status_code=403, detail="Access not yet approved")

    owner_id = nk.get("owner_id")
    if not owner_id:
        raise HTTPException(status_code=404, detail="No linked owner")
    return nk, {"owner_id": owner_id}

def to_out(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc["id"] = str(doc.pop("_id"))
    return doc
