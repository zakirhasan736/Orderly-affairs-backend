"""Hash email OTP codes at rest (password reset, login MFA, etc.)."""

from __future__ import annotations

import hashlib
import hmac

from app.security.crypto import KEY


def _otp_scope(email: str, otp_type: str) -> str:
    return f"{email.lower().strip()}:{otp_type or 'default'}"


def hash_otp_value(email: str, otp: int | str, otp_type: str = "") -> str:
    message = f"{_otp_scope(email, otp_type)}:{otp}".encode("utf-8")
    return hmac.new(KEY, message, hashlib.sha256).hexdigest()


def otp_storage_fields(email: str, otp: int, otp_type: str = "") -> dict:
    """Fields to store in MongoDB instead of plaintext ``otp``."""
    return {
        "email": email.lower().strip(),
        "otp_hash": hash_otp_value(email, otp, otp_type),
        "type": otp_type,
    }


def verify_stored_otp(record: dict, otp: int | str) -> bool:
    if not record:
        return False
    email = record.get("email") or ""
    otp_type = record.get("type") or ""
    stored_hash = record.get("otp_hash")
    if stored_hash:
        expected = hash_otp_value(email, otp, otp_type)
        return hmac.compare_digest(stored_hash, expected)
    legacy = record.get("otp")
    if legacy is None:
        return False
    return str(legacy) == str(otp)
