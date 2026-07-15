"""Rate limiting for authentication endpoints."""

from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from app.config import settings
from app.database import auth_rate_limits_collection


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


async def enforce_auth_rate_limit(
    request: Request,
    *,
    key: str,
    max_attempts: int | None = None,
    window_minutes: int | None = None,
) -> None:
    if settings.APP_ENV == "development":
        return

    limit = max_attempts or settings.AUTH_RATE_LIMIT_MAX_ATTEMPTS
    window = window_minutes or settings.AUTH_RATE_LIMIT_WINDOW_MINUTES
    now = datetime.utcnow()
    window_start = now - timedelta(minutes=window)
    ip = _client_ip(request)
    doc_key = f"{key}:{ip}"

    record = await auth_rate_limits_collection.find_one({"key": doc_key})
    if not record:
        await auth_rate_limits_collection.insert_one(
            {
                "key": doc_key,
                "attempts": [{"at": now}],
                "updated_at": now,
            }
        )
        return

    attempts = []
    for attempt in record.get("attempts", []):
        at = _as_naive_utc(attempt.get("at"))
        if at is not None and at >= window_start:
            attempts.append({"at": at})

    if len(attempts) >= limit:
        oldest = min((a["at"] for a in attempts), default=now)
        retry_after = int((oldest + timedelta(minutes=window) - now).total_seconds())
        retry_after = max(retry_after, 1)
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many attempts. Please try again in {retry_after} seconds."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    attempts.append({"at": now})
    await auth_rate_limits_collection.update_one(
        {"key": doc_key},
        {"$set": {"attempts": attempts, "updated_at": now}},
    )


async def reset_auth_rate_limit(request: Request, *, key: str) -> None:
    ip = _client_ip(request)
    await auth_rate_limits_collection.delete_one({"key": f"{key}:{ip}"})
