"""Single-use vault-unlock claim tokens for next of kin / executor after verification."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from app.auth.vault_unlock_timings import CLAIM_TOKEN_TTL

# Re-export for tests and callers.


def generate_claim_token() -> str:
    return secrets.token_urlsafe(32)


def hash_claim_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(plain: str, stored_hash: str) -> bool:
    if not plain or not stored_hash:
        return False
    return hmac.compare_digest(hash_claim_token(plain), stored_hash)


def claim_expiry(now: datetime | None = None) -> datetime:
    stamp = now or datetime.now(timezone.utc)
    return stamp + CLAIM_TOKEN_TTL


def claim_is_expired(expires_at, now: datetime | None = None) -> bool:
    if not expires_at:
        return True
    stamp = now or datetime.now(timezone.utc)
    if getattr(expires_at, "tzinfo", None) is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp > expires_at
