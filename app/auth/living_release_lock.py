"""Lockouts for owner living-access release (Rev 2 Step 2)."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException

from app.database import users_collection

RELEASE_MAX_FAILURES = 5
RELEASE_LOCKOUT = timedelta(minutes=15)


def _as_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


async def assert_living_release_unlocked(owner: dict) -> None:
    until = _as_naive(owner.get("living_release_locked_until"))
    if until and until > datetime.utcnow():
        minutes = max(1, int((until - datetime.utcnow()).total_seconds() / 60))
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many failed password attempts for this action. "
                f"Try again in about {minutes} minutes."
            ),
        )


async def record_living_release_failure(owner: dict) -> None:
    now = datetime.utcnow()
    fails = int(owner.get("living_release_failures") or 0) + 1
    fields: dict = {
        "living_release_failures": fails,
        "updated_at": now,
    }
    if fails >= RELEASE_MAX_FAILURES:
        fields["living_release_locked_until"] = now + RELEASE_LOCKOUT
        fields["living_release_failures"] = 0
    await users_collection.update_one({"_id": owner["_id"]}, {"$set": fields})
    owner.update(fields)
    if fails >= RELEASE_MAX_FAILURES:
        raise HTTPException(
            status_code=429,
            detail=(
                "Too many failed password attempts for this action. "
                "Try again in 15 minutes."
            ),
        )


async def clear_living_release_failures(owner: dict) -> None:
    await users_collection.update_one(
        {"_id": owner["_id"]},
        {
            "$set": {"living_release_failures": 0, "updated_at": datetime.utcnow()},
            "$unset": {"living_release_locked_until": ""},
        },
    )
