from fastapi import HTTPException, Request
from typing import Dict, Any, Tuple
from datetime import datetime
from bson import ObjectId
from app.security.cookie_auth import NOK_ACCESS_COOKIE
from app.security.token_resolver import decode_access_token
from app.database import users_collection, kits_collection
from app.security.kit_data_crypto import load_kit_document

from app.security.access_control import nok_has_section_access
from app.security.vault_principals import require_nok_principal

async def require_owner(request: Request, authorization: str | None = None):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner token required")
    owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    return owner

async def require_nok(request: Request, authorization: str | None = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    decoded = decode_access_token(request, authorization, access_cookie=NOK_ACCESS_COOKIE)
    if decoded.get("role") != "nextkin":
        raise HTTPException(status_code=403, detail="Next-of-Kin token required")

    nk = await users_collection.find_one({"email": decoded["email"], "role": "nextkin"})
    if not nk and decoded.get("sub"):
        try:
            nk = await users_collection.find_one(
                {"_id": ObjectId(str(decoded["sub"])), "role": "nextkin"}
            )
        except Exception:
            nk = None
    if not nk:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found")
    if not nk.get("immediate_access", False):
        raise HTTPException(status_code=403, detail="Access not yet approved")

    require_nok_principal(nk)

    owner_id = nk.get("owner_id")
    if not owner_id:
        raise HTTPException(status_code=404, detail="No linked owner")
    return nk, {"owner_id": owner_id}

async def get_or_init_kit(owner_id: str) -> Dict[str, Any]:
    kit = await kits_collection.find_one({"owner_id": owner_id})
    if kit:
        return load_kit_document(kit)
    now = datetime.utcnow()
    kit = {
        "owner_id": owner_id,
        "sections": [],
        "disabled_sections": {},
        "disabled_subsections": {},
        "created_at": now,
        "updated_at": now,
    }
    await kits_collection.insert_one(kit)
    return kit

def ensure_section_struct(kit: Dict[str, Any], section_id: str) -> None:
    if not any(s.get("id") == section_id for s in kit["sections"]):
        kit["sections"].append({"id": section_id, "title": "", "data": {}, "subsections": []})

def ensure_subsection_struct(kit: Dict[str, Any], section_id: str, sub_id: str) -> None:
    ensure_section_struct(kit, section_id)
    for s in kit["sections"]:
        if s["id"] == section_id:
            if not any(ss.get("id") == sub_id for ss in s["subsections"]):
                s["subsections"].append({"id": sub_id, "title": "", "data": {}, "version": 1})
            break

def filter_sections_for_nok(kit: Dict[str, Any], nk: Dict[str, Any]) -> Dict[str, Any]:
    """Apply NOK access — Full Kit still excludes owner-management sections 2/3/4."""
    filtered = {
        **kit,
        "sections": [
            s
            for s in kit.get("sections") or []
            if nok_has_section_access(nk, str(s.get("id") or ""))
        ],
    }
    return filtered
