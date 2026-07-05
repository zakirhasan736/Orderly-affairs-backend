"""Rate limiting for authentication endpoints."""

from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from app.config import settings
from app.database import auth_rate_limits_collection


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


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

    attempts = [
        attempt
        for attempt in record.get("attempts", [])
        if attempt.get("at", now) >= window_start
    ]

    if len(attempts) >= limit:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please try again later.",
        )

    attempts.append({"at": now})
    await auth_rate_limits_collection.update_one(
        {"key": doc_key},
        {"$set": {"attempts": attempts, "updated_at": now}},
    )


async def reset_auth_rate_limit(request: Request, *, key: str) -> None:
    ip = _client_ip(request)
    await auth_rate_limits_collection.delete_one({"key": f"{key}:{ip}"})
