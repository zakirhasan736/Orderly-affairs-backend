"""Rate limiting for vault and data API routes."""

from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.database import auth_rate_limits_collection
from app.security.auth_rate_limit import _client_ip

VAULT_PATH_PREFIXES = (
    "/sections/",
    "/kit",
    "/uploads",
    "/message",
    "/nok-letter",
    "/ai/",
)

API_RATE_LIMIT_MAX = 120
API_RATE_LIMIT_WINDOW_MINUTES = 1


async def enforce_api_rate_limit(request: Request, *, key: str) -> None:
    if settings.APP_ENV == "development":
        return

    now = datetime.utcnow()
    window_start = now - timedelta(minutes=API_RATE_LIMIT_WINDOW_MINUTES)
    ip = _client_ip(request)
    doc_key = f"api:{key}:{ip}"

    record = await auth_rate_limits_collection.find_one({"key": doc_key})
    if not record:
        await auth_rate_limits_collection.insert_one(
            {"key": doc_key, "attempts": [{"at": now}], "updated_at": now}
        )
        return

    attempts = [
        attempt
        for attempt in record.get("attempts", [])
        if attempt.get("at", now) >= window_start
    ]

    if len(attempts) >= API_RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please slow down.",
        )

    attempts.append({"at": now})
    await auth_rate_limits_collection.update_one(
        {"key": doc_key},
        {"$set": {"attempts": attempts, "updated_at": now}},
    )


class VaultApiRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(prefix) for prefix in VAULT_PATH_PREFIXES):
            await enforce_api_rate_limit(request, key=path.split("/")[1] or "vault")
        return await call_next(request)
