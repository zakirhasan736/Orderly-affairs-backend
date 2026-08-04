"""Owner / NOK E2EE key envelopes — server stores wrapped DEK only (never plaintext DEK)."""

from __future__ import annotations

from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.database import users_collection
from app.security.token_resolver import decode_access_token, decode_owner_or_nok_token
from app.security.cookie_auth import OWNER_ACCESS_COOKIE

e2ee_router = APIRouter(prefix="/auth/e2ee", tags=["e2ee"])


class E2eeSetupBody(BaseModel):
    """Client generates DEK, wraps with password-derived key, sends wrap only."""

    salt_b64: str = Field(min_length=8, max_length=128)
    wrapped_dek_b64: str = Field(min_length=16, max_length=4096)
    kdf: str = "PBKDF2-SHA256"
    kdf_iterations: int = Field(default=310000, ge=100000, le=5_000_000)
    wrap_alg: str = "AES-GCM"


class E2eeNokWrapBody(BaseModel):
    nok_user_id: str
    salt_b64: str = Field(min_length=8, max_length=128)
    wrapped_dek_b64: str = Field(min_length=16, max_length=4096)
    kdf: str = "PBKDF2-SHA256"
    kdf_iterations: int = Field(default=310000, ge=100000, le=5_000_000)
    wrap_alg: str = "AES-GCM"


def _e2ee_enabled() -> bool:
    return bool(getattr(settings, "E2EE_ENABLED", True))


@e2ee_router.get("/status")
async def e2ee_status(
    request: Request,
    authorization: str | None = Header(default=None),
):
    if not _e2ee_enabled():
        return {"enabled": False, "configured": False, "reason": "E2EE_ENABLED=false"}

    decoded = decode_owner_or_nok_token(request, authorization)
    role = decoded.get("role")

    if role == "owner":
        user = await users_collection.find_one(
            {"email": decoded.get("sub"), "role": "owner"}
        )
        if not user:
            raise HTTPException(401, "Unauthorized")
        env = (user.get("e2ee") or {}) if isinstance(user.get("e2ee"), dict) else {}
        configured = bool(env.get("wrapped_dek_b64") and env.get("salt_b64"))
        return {
            "enabled": True,
            "role": "owner",
            "configured": configured,
            "needs_setup": bool(env.get("needs_setup")) or not configured,
            "kdf": env.get("kdf"),
            "kdf_iterations": env.get("kdf_iterations"),
            "wrap_alg": env.get("wrap_alg"),
            "salt_b64": env.get("salt_b64"),
            "wrapped_dek_b64": env.get("wrapped_dek_b64"),
            "updated_at": env.get("updated_at"),
        }

    if role == "nextkin":
        user = await users_collection.find_one(
            {"_id": ObjectId(decoded.get("sub")), "role": "nextkin"}
        )
        if not user:
            raise HTTPException(401, "Unauthorized")
        env = (
            (user.get("e2ee_wrap") or {})
            if isinstance(user.get("e2ee_wrap"), dict)
            else {}
        )
        return {
            "enabled": True,
            "role": "nextkin",
            "configured": bool(env.get("wrapped_dek_b64") and env.get("salt_b64")),
            "kdf": env.get("kdf"),
            "kdf_iterations": env.get("kdf_iterations"),
            "wrap_alg": env.get("wrap_alg"),
            "salt_b64": env.get("salt_b64"),
            "wrapped_dek_b64": env.get("wrapped_dek_b64"),
            "owner_id": user.get("owner_id"),
        }

    raise HTTPException(403, "Owner or Next-of-Kin only")


@e2ee_router.post("/setup")
async def e2ee_setup(
    body: E2eeSetupBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    if not _e2ee_enabled():
        raise HTTPException(400, "E2EE is disabled on this server")

    decoded = decode_access_token(
        request, authorization, access_cookie=OWNER_ACCESS_COOKIE
    )
    if decoded.get("role") != "owner":
        raise HTTPException(403, "Owner only")

    user = await users_collection.find_one(
        {"email": decoded.get("sub"), "role": "owner"}
    )
    if not user:
        raise HTTPException(401, "Unauthorized")

    now = datetime.utcnow()
    envelope = {
        "salt_b64": body.salt_b64.strip(),
        "wrapped_dek_b64": body.wrapped_dek_b64.strip(),
        "kdf": body.kdf,
        "kdf_iterations": body.kdf_iterations,
        "wrap_alg": body.wrap_alg,
        "updated_at": now,
        "version": 1,
        "needs_setup": False,
    }
    await users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"e2ee": envelope, "e2ee_enabled": True, "updated_at": now}},
    )
    return {"message": "E2EE envelope stored", "configured": True}


@e2ee_router.post("/rewrap")
async def e2ee_rewrap(
    body: E2eeSetupBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """
    Replace owner password wrap for the same DEK (password change while vault unlocked).
    Client must already hold the DEK and send a new wrap — server never sees DEK.
    """
    if not _e2ee_enabled():
        raise HTTPException(400, "E2EE is disabled on this server")

    decoded = decode_access_token(
        request, authorization, access_cookie=OWNER_ACCESS_COOKIE
    )
    if decoded.get("role") != "owner":
        raise HTTPException(403, "Owner only")

    user = await users_collection.find_one(
        {"email": decoded.get("sub"), "role": "owner"}
    )
    if not user:
        raise HTTPException(401, "Unauthorized")

    existing = user.get("e2ee") if isinstance(user.get("e2ee"), dict) else {}
    if not existing.get("wrapped_dek_b64"):
        raise HTTPException(400, "E2EE not configured — use /auth/e2ee/setup")

    now = datetime.utcnow()
    envelope = {
        "salt_b64": body.salt_b64.strip(),
        "wrapped_dek_b64": body.wrapped_dek_b64.strip(),
        "kdf": body.kdf,
        "kdf_iterations": body.kdf_iterations,
        "wrap_alg": body.wrap_alg,
        "updated_at": now,
        "version": int(existing.get("version") or 1),
        "needs_setup": False,
        "rewrapped_at": now,
    }
    await users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"e2ee": envelope, "updated_at": now}},
    )
    return {"message": "E2EE wrap updated", "configured": True}


@e2ee_router.get("/migration-status")
async def e2ee_migration_status(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Count vault sections still on server AES (v2) vs client E2EE (v3)."""
    if not _e2ee_enabled():
        return {"enabled": False, "legacy_v2": 0, "e2ee_v3": 0}

    from app.database import section_data_collection
    from app.security.section_e2ee import E2EE_VERSION

    decoded = decode_owner_or_nok_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(403, "Owner only")

    user = await users_collection.find_one(
        {"email": decoded.get("sub"), "role": "owner"}
    )
    if not user:
        raise HTTPException(401, "Unauthorized")

    owner_id = str(user["_id"])
    legacy_v2 = await section_data_collection.count_documents(
        {
            "owner_id": owner_id,
            "encrypted_data": {"$exists": True, "$ne": None},
            "$or": [
                {"encryption_version": {"$exists": False}},
                {"encryption_version": {"$ne": E2EE_VERSION}},
            ],
        }
    )
    e2ee_v3 = await section_data_collection.count_documents(
        {
            "owner_id": owner_id,
            "encryption_version": E2EE_VERSION,
            "encrypted_data": {"$exists": True, "$ne": None},
        }
    )
    return {
        "enabled": True,
        "legacy_v2": legacy_v2,
        "e2ee_v3": e2ee_v3,
        "migration_complete": legacy_v2 == 0 and e2ee_v3 >= 0,
    }


@e2ee_router.post("/nok-wrap")
async def e2ee_nok_wrap(
    body: E2eeNokWrapBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Owner stores a DEK wrap for a Next-of-Kin (derived from NOK card password)."""
    if not _e2ee_enabled():
        raise HTTPException(400, "E2EE is disabled on this server")

    decoded = decode_access_token(
        request, authorization, access_cookie=OWNER_ACCESS_COOKIE
    )
    if decoded.get("role") != "owner":
        raise HTTPException(403, "Owner only")

    owner = await users_collection.find_one(
        {"email": decoded.get("sub"), "role": "owner"}
    )
    if not owner:
        raise HTTPException(401, "Unauthorized")

    if not ObjectId.is_valid(body.nok_user_id):
        raise HTTPException(400, "Invalid nok_user_id")

    nok = await users_collection.find_one(
        {
            "_id": ObjectId(body.nok_user_id),
            "role": "nextkin",
            "owner_id": str(owner["_id"]),
        }
    )
    if not nok:
        raise HTTPException(404, "Next-of-Kin not found")

    now = datetime.utcnow()
    wrap = {
        "salt_b64": body.salt_b64.strip(),
        "wrapped_dek_b64": body.wrapped_dek_b64.strip(),
        "kdf": body.kdf,
        "kdf_iterations": body.kdf_iterations,
        "wrap_alg": body.wrap_alg,
        "updated_at": now,
        "version": 1,
    }
    await users_collection.update_one(
        {"_id": nok["_id"]},
        {"$set": {"e2ee_wrap": wrap, "updated_at": now}},
    )
    return {"message": "NOK E2EE wrap stored", "nok_user_id": body.nok_user_id}
