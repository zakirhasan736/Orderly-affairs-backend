# app/auth/phone.py
import re
from datetime import datetime
from typing import Any

import phonenumbers


PHONE_ALREADY_IN_USE = (
    "This phone number is already linked to another account. "
    "Sign in with that account, or use a different number."
)


def _normalize_raw_phone(phone: str) -> str:
    raw = str(phone).strip()
    if not raw:
        return raw

    if raw.startswith("+"):
        return raw

    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw

    return f"+{digits}"


def format_phone(phone: str, default_region: str = "US") -> str:
    if not phone or not str(phone).strip():
        raise ValueError("Phone number is required")

    raw = _normalize_raw_phone(phone)

    parse_attempts = [
        (raw, None),
        (raw, default_region),
        (str(phone).strip(), default_region),
    ]

    last_error: phonenumbers.NumberParseException | None = None

    for candidate, region in parse_attempts:
        if not candidate:
            continue

        try:
            parsed = phonenumbers.parse(candidate, region)

            if not phonenumbers.is_valid_number(parsed):
                continue

            return phonenumbers.format_number(
                parsed,
                phonenumbers.PhoneNumberFormat.E164,
            )
        except phonenumbers.NumberParseException as exc:
            last_error = exc
            continue

    if last_error:
        raise ValueError(
            "Invalid phone format. Select your country code and enter a valid phone number."
        ) from last_error

    raise ValueError(
        "Invalid phone format. Select your country code and enter a valid phone number."
    )


def looks_like_email(value: str) -> bool:
    return "@" in (value or "").strip()


def looks_like_phone_identifier(value: str) -> bool:
    """True when the login identifier is clearly a phone (not an email)."""
    raw = (value or "").strip()
    if not raw or looks_like_email(raw):
        return False
    digits = re.sub(r"\D", "", raw)
    return len(digits) >= 8


async def ensure_phone_available(
    phone: str,
    *,
    users_collection: Any,
    pending_signup_collection: Any | None = None,
    exclude_user_id: Any | None = None,
    exclude_pending_email: str | None = None,
) -> None:
    """Raise if E.164 phone is used by another owner, pending signup, or closed account."""
    from fastapi import HTTPException

    from app.auth.deleted_account_registry import assert_identity_not_deleted

    await assert_identity_not_deleted(phone=phone)

    query: dict = {"phone": phone, "role": "owner"}
    if exclude_user_id is not None:
        query["_id"] = {"$ne": exclude_user_id}

    existing = await users_collection.find_one(query, {"_id": 1, "email": 1})
    if existing:
        raise HTTPException(status_code=400, detail=PHONE_ALREADY_IN_USE)

    if pending_signup_collection is not None:
        pending_query: dict = {
            "phone": phone,
            "expires_at": {"$gt": datetime.utcnow()},
        }
        if exclude_pending_email:
            pending_query["email"] = {
                "$ne": exclude_pending_email.lower().strip()
            }
        pending = await pending_signup_collection.find_one(
            pending_query, {"_id": 1, "email": 1}
        )
        if pending:
            raise HTTPException(status_code=400, detail=PHONE_ALREADY_IN_USE)


async def find_owner_by_login_identifier(
    identifier: str,
    *,
    users_collection: Any,
) -> dict | None:
    """Resolve an owner by email or phone number."""
    raw = (identifier or "").strip()
    if not raw:
        return None

    if looks_like_email(raw):
        return await users_collection.find_one(
            {"email": raw.lower(), "role": "owner"}
        )

    if looks_like_phone_identifier(raw):
        try:
            phone = format_phone(raw)
        except ValueError:
            return None
        return await users_collection.find_one(
            {"phone": phone, "role": "owner"}
        )

    # Fallback: try email-style lowercase match for odd identifiers
    return await users_collection.find_one(
        {"email": raw.lower(), "role": "owner"}
    )


async def ensure_owner_phone_index(users_collection: Any) -> None:
    """Unique phone per owner (sparse — accounts without phone are allowed)."""
    await users_collection.create_index(
        [("phone", 1)],
        name="owner_phone_unique",
        unique=True,
        partialFilterExpression={
            "role": "owner",
            "phone": {"$type": "string"},
        },
    )
