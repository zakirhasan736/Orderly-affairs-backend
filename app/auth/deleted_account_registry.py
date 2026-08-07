"""Retain minimal hashed identity after hard account delete for rejoin detection."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime
from typing import Any

from app.config import settings
from app.database import deleted_accounts_collection

ACCOUNT_CLOSED_DETAIL = (
    "This account was closed and cannot be reopened with the same email or phone. "
    "Contact support if you believe this is a mistake."
)


def _pepper() -> bytes:
    raw = (
        (os.getenv("AES_256_KEY") or "").strip()
        or (settings.BACKUP_ENCRYPTION_KEY or os.getenv("BACKUP_ENCRYPTION_KEY") or "").strip()
        or "orderly-affairs-deleted-account-pepper"
    )
    return raw.encode("utf-8")


def hash_identity(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return hmac.new(_pepper(), text.encode("utf-8"), hashlib.sha256).hexdigest()


def email_hint(email: str | None) -> str | None:
    text = str(email or "").strip().lower()
    if "@" not in text:
        return None
    local, _, domain = text.partition("@")
    if not local or not domain:
        return None
    keep = local[:2] if len(local) >= 2 else local[:1]
    return f"{keep}***@{domain}"


def phone_hint(phone: str | None) -> str | None:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) < 4:
        return None
    return f"***{digits[-4:]}"


def build_deleted_account_record(
    owner: dict,
    *,
    deleted_by: str,
    reason: str | None = None,
    deleted_by_email: str | None = None,
) -> dict[str, Any]:
    email = str(owner.get("email") or "").strip().lower()
    phone = str(owner.get("phone") or owner.get("phone_number") or "").strip()
    full_name = str(
        owner.get("full_name") or owner.get("name") or ""
    ).strip()
    billing = owner.get("billing") if isinstance(owner.get("billing"), dict) else {}

    return {
        "former_user_id": str(owner.get("_id") or ""),
        "email_hash": hash_identity(email),
        "phone_hash": hash_identity(phone),
        "full_name_hash": hash_identity(full_name),
        "stripe_customer_hash": hash_identity(billing.get("customer_id")),
        "email_hint": email_hint(email),
        "phone_hint": phone_hint(phone),
        "folder_uuid": owner.get("folder_uuid"),
        "deleted_by": deleted_by,  # "self" | "admin"
        "deleted_by_email": (deleted_by_email or "").strip().lower() or None,
        "reason": (reason or "").strip()[:500] or None,
        "block_rejoin": True,
        "deleted_at": datetime.utcnow(),
    }


async def record_deleted_account(
    owner: dict,
    *,
    deleted_by: str,
    reason: str | None = None,
    deleted_by_email: str | None = None,
) -> dict[str, Any]:
    doc = build_deleted_account_record(
        owner,
        deleted_by=deleted_by,
        reason=reason,
        deleted_by_email=deleted_by_email,
    )
    # One active tombstone per email hash (latest wins).
    if doc.get("email_hash"):
        await deleted_accounts_collection.update_one(
            {"email_hash": doc["email_hash"]},
            {"$set": doc},
            upsert=True,
        )
    else:
        await deleted_accounts_collection.insert_one(doc)
    return doc


async def find_blocked_deleted_account(
    *,
    email: str | None = None,
    phone: str | None = None,
) -> dict | None:
    clauses: list[dict[str, Any]] = []
    email_hash = hash_identity(email)
    phone_hash = hash_identity(phone)
    if email_hash:
        clauses.append({"email_hash": email_hash})
    if phone_hash:
        clauses.append({"phone_hash": phone_hash})
    if not clauses:
        return None
    return await deleted_accounts_collection.find_one(
        {"block_rejoin": True, "$or": clauses},
    )


async def assert_identity_not_deleted(
    *,
    email: str | None = None,
    phone: str | None = None,
) -> None:
    from fastapi import HTTPException

    hit = await find_blocked_deleted_account(email=email, phone=phone)
    if hit:
        raise HTTPException(status_code=400, detail=ACCOUNT_CLOSED_DETAIL)
