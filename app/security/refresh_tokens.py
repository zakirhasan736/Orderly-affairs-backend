"""Opaque refresh-token storage with rotation and family revocation."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from secrets import token_urlsafe

from bson import ObjectId

from app.config import settings
from app.database import refresh_tokens_collection


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_refresh_token(
    *,
    user_id: str,
    role: str,
    email: str,
    family_id: str | None = None,
) -> tuple[str, str]:
    plain = token_urlsafe(48)
    family = family_id or token_urlsafe(16)
    expires = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    await refresh_tokens_collection.insert_one(
        {
            "user_id": user_id,
            "role": role,
            "email": email.lower().strip() if email else None,
            "token_hash": _hash_token(plain),
            "family_id": family,
            "expires_at": expires,
            "revoked": False,
            "created_at": datetime.utcnow(),
        }
    )
    return plain, family


async def _revoke_family(family_id: str) -> None:
    await refresh_tokens_collection.update_many(
        {"family_id": family_id, "revoked": False},
        {"$set": {"revoked": True, "revoked_at": datetime.utcnow()}},
    )


async def rotate_refresh_token(plain: str) -> tuple[str, dict] | None:
    token_hash = _hash_token(plain)
    record = await refresh_tokens_collection.find_one({"token_hash": token_hash})

    if not record:
        return None

    if record.get("revoked"):
        await _revoke_family(record["family_id"])
        return None

    if record["expires_at"] <= datetime.utcnow():
        await refresh_tokens_collection.update_one(
            {"_id": record["_id"]},
            {"$set": {"revoked": True, "revoked_at": datetime.utcnow()}},
        )
        return None

    await refresh_tokens_collection.update_one(
        {"_id": record["_id"]},
        {"$set": {"revoked": True, "revoked_at": datetime.utcnow()}},
    )

    new_plain, _ = await create_refresh_token(
        user_id=record["user_id"],
        role=record["role"],
        email=record.get("email") or "",
        family_id=record["family_id"],
    )

    return new_plain, {
        "user_id": record["user_id"],
        "role": record["role"],
        "email": record.get("email"),
        "family_id": record["family_id"],
    }


async def revoke_refresh_token(plain: str) -> None:
    token_hash = _hash_token(plain)
    await refresh_tokens_collection.update_one(
        {"token_hash": token_hash},
        {"$set": {"revoked": True, "revoked_at": datetime.utcnow()}},
    )


async def revoke_all_user_refresh_tokens(
    user_id: str,
    *,
    role: str | None = None,
) -> None:
    query: dict = {"user_id": user_id, "revoked": False}
    if role:
        query["role"] = role
    await refresh_tokens_collection.update_many(
        query,
        {"$set": {"revoked": True, "revoked_at": datetime.utcnow()}},
    )


async def resolve_user_from_id(user_id: str, role: str):
    from app.database import users_collection

    if role == "nextkin":
        return await users_collection.find_one(
            {"_id": ObjectId(user_id), "role": "nextkin"}
        )
    return await users_collection.find_one(
        {"_id": ObjectId(user_id), "role": "owner"}
    )
