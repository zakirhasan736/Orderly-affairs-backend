"""Rate limiting for authentication endpoints.

Progressive lock (never multi-hour):
  1st trip → 45s
  2nd → 5 minutes
  3rd+ → 15 minutes
Hard ceiling → 30 minutes
"""

from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from app.config import settings
from app.database import auth_rate_limits_collection

_LOCK_STEPS_SECONDS = (45, 5 * 60, 15 * 60)  # 45s → 5m → 15m
_HARD_MAX_LOCK_SECONDS = 30 * 60  # 30 minutes absolute ceiling


def _client_ip(request: Request) -> str:
    cf_ip = (request.headers.get("CF-Connecting-IP") or "").strip()
    if cf_ip:
        return cf_ip
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _as_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def _lock_duration_for_level(level: int) -> int:
    idx = max(0, min(int(level), len(_LOCK_STEPS_SECONDS) - 1))
    return min(_LOCK_STEPS_SECONDS[idx], _HARD_MAX_LOCK_SECONDS)


async def enforce_auth_rate_limit(
    request: Request,
    *,
    key: str,
    max_attempts: int | None = None,
    window_minutes: int | None = None,
) -> None:
    if settings.is_development:
        return

    limit = max_attempts or settings.AUTH_RATE_LIMIT_MAX_ATTEMPTS
    window = window_minutes or settings.AUTH_RATE_LIMIT_WINDOW_MINUTES
    now = datetime.utcnow()
    window_start = now - timedelta(minutes=window)
    ip = _client_ip(request)
    doc_key = f"{key}:{ip}"

    record = await auth_rate_limits_collection.find_one({"key": doc_key})

    # Still locked from a previous "too many attempts"
    if record:
        locked_until = _as_naive_utc(record.get("locked_until"))
        if locked_until is not None and locked_until > now:
            retry_after = int((locked_until - now).total_seconds())
            retry_after = min(max(retry_after, 1), _HARD_MAX_LOCK_SECONDS)
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Too many attempts. Please try again in {retry_after} seconds."
                ),
                headers={"Retry-After": str(retry_after)},
            )

    if not record:
        await auth_rate_limits_collection.insert_one(
            {
                "key": doc_key,
                "attempts": [{"at": now}],
                "lock_level": 0,
                "locked_until": None,
                "updated_at": now,
            }
        )
        return

    attempts = []
    for attempt in record.get("attempts", []):
        at = _as_naive_utc(attempt.get("at"))
        if at is not None and window_start <= at <= now:
            attempts.append({"at": at})

    if len(attempts) >= limit:
        level = int(record.get("lock_level") or 0)
        lock_secs = _lock_duration_for_level(level)
        next_level = min(level + 1, len(_LOCK_STEPS_SECONDS) - 1)
        locked_until = now + timedelta(seconds=lock_secs)

        await auth_rate_limits_collection.update_one(
            {"key": doc_key},
            {
                "$set": {
                    "attempts": attempts,
                    "lock_level": next_level,
                    "locked_until": locked_until,
                    "updated_at": now,
                }
            },
        )
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many attempts. Please try again in {lock_secs} seconds."
            ),
            headers={"Retry-After": str(lock_secs)},
        )

    attempts.append({"at": now})
    update: dict = {
        "attempts": attempts,
        "updated_at": now,
        "locked_until": None,
    }
    # Quiet success path: ease escalation back down
    if len(attempts) <= max(2, limit // 4):
        update["lock_level"] = 0

    await auth_rate_limits_collection.update_one(
        {"key": doc_key},
        {"$set": update},
    )


async def reset_auth_rate_limit(request: Request, *, key: str) -> None:
    ip = _client_ip(request)
    await auth_rate_limits_collection.delete_one({"key": f"{key}:{ip}"})
