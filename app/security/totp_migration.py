"""One-time migration: encrypt plaintext TOTP secrets at rest."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.database import pending_signup_collection, users_collection
from app.security.crypto import is_encrypted_payload
from app.security.totp_crypto import encrypt_totp_value


def _needs_encryption(value: Any) -> bool:
    if not value or not isinstance(value, str):
        return False
    if value in {"", "null"}:
        return False
    return not is_encrypted_payload(value)


async def migrate_user_totp_secrets() -> int:
    migrated = 0
    async for user in users_collection.find({}):
        email = (user.get("email") or "").lower().strip()
        if not email:
            continue

        updates: dict[str, Any] = {}

        totp = user.get("totp_secret")
        if _needs_encryption(totp):
            updates["totp_secret"] = encrypt_totp_value(email, totp)

        provisioned = user.get("provisioned_secret")
        if _needs_encryption(provisioned):
            updates["provisioned_secret"] = encrypt_totp_value(
                email,
                provisioned,
                pending=True,
            )

        if not updates:
            continue

        updates["updated_at"] = datetime.utcnow()
        await users_collection.update_one({"_id": user["_id"]}, {"$set": updates})
        migrated += 1

    return migrated


async def migrate_pending_totp_secrets() -> int:
    migrated = 0
    async for pending in pending_signup_collection.find({}):
        email = (pending.get("email") or "").lower().strip()
        if not email:
            continue

        updates: dict[str, Any] = {}

        totp = pending.get("totp_secret")
        if _needs_encryption(totp):
            updates["totp_secret"] = encrypt_totp_value(email, totp)

        provisioned = pending.get("provisioned_secret")
        if _needs_encryption(provisioned):
            updates["provisioned_secret"] = encrypt_totp_value(
                email,
                provisioned,
                pending=True,
            )

        if not updates:
            continue

        await pending_signup_collection.update_one(
            {"_id": pending["_id"]},
            {"$set": updates},
        )
        migrated += 1

    return migrated


async def run_totp_encryption_migration() -> dict[str, int]:
    return {
        "users": await migrate_user_totp_secrets(),
        "pending_signups": await migrate_pending_totp_secrets(),
    }
