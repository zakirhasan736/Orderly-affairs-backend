# app/letters/core.py
from fastapi import HTTPException
from datetime import datetime
from typing import Dict, Any, Tuple
from bson import ObjectId
from app.database import users_collection, letters_collection
from app.security.jwt_handler import verify_token

async def require_owner(auth: str | None) -> Dict[str, Any]:
    if not auth: raise HTTPException(401, "Missing token")
    decoded = verify_token(auth.split(" ")[1])
    if not decoded or decoded.get("role") != "owner":
        raise HTTPException(403, "Owner token required")
    owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
    if not owner: raise HTTPException(404, "Owner not found")
    return owner

async def require_nok(auth: str | None) -> Tuple[Dict[str, Any], str]:
    if not auth: raise HTTPException(401, "Missing token")
    decoded = verify_token(auth.split(" ")[1])
    if not decoded or decoded.get("role") != "nextkin":
        raise HTTPException(403, "Next-of-Kin token required")
    nk = await users_collection.find_one({"email": decoded["email"], "role": "nextkin"})
    if not nk: raise HTTPException(404, "Next-of-Kin not found")
    if not nk.get("immediate_access", False):
        raise HTTPException(403, "Access not yet approved")
    owner_id = str(nk.get("owner_id") or "")
    if not owner_id: raise HTTPException(404, "No linked owner")
    return nk, owner_id

def to_out(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc["id"] = str(doc.pop("_id"))
    return doc
