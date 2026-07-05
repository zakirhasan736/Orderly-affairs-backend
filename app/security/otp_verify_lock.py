"""Shared OTP verify lockout (email MFA, password reset, etc.)."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from app.auth.otp_security import get_client_ip, get_session_id, get_user_agent, log_otp_event


def verify_lock_key(scope: str, email: str) -> str:
    return f"{scope}|{email.lower().strip()}"


async def ensure_otp_verify_not_locked(scope: str, email: str) -> None:
    key = verify_lock_key(scope, email)
    lock = await otp_verify_locks_collection.find_one({"key": key})
    if not lock:
        return

    locked_until = lock.get("lockedUntil")
    if locked_until and locked_until > datetime.utcnow():
        remaining = int((locked_until - datetime.utcnow()).total_seconds())
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many incorrect attempts. Try again in "
                f"{max(remaining, 1)} seconds."
            ),
            headers={"Retry-After": str(max(remaining, 1))},
        )

    if locked_until and locked_until <= datetime.utcnow():
        await otp_verify_locks_collection.delete_one({"key": key})


async def record_otp_verify_attempt(
    *,
    request: Request,
    scope: str,
    email: str,
    success: bool,
    session_id: str | None = None,
    generic_error: str = "Invalid or expired code",
) -> None:
    normalized = email.lower().strip()
    key = verify_lock_key(scope, normalized)
    ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    resolved_session = get_session_id(request, session_id)

    await log_otp_event(
        channel="email",
        email=normalized,
        ip=ip,
        user_agent=user_agent,
        session_id=resolved_session,
        captcha_passed=True,
        status="verified" if success else "failed",
        action=f"verify:{scope}",
    )

    if success:
        await otp_verify_locks_collection.delete_one({"key": key})
        return

    lock = await otp_verify_locks_collection.find_one({"key": key})
    failed_attempts = int((lock or {}).get("failedAttempts", 0)) + 1

    if failed_attempts >= settings.OTP_VERIFY_MAX_ATTEMPTS:
        locked_until = datetime.utcnow() + timedelta(
            minutes=settings.OTP_VERIFY_LOCK_MINUTES
        )
        await otp_verify_locks_collection.update_one(
            {"key": key},
            {
                "$set": {
                    "email": normalized,
                    "failedAttempts": failed_attempts,
                    "lockedUntil": locked_until,
                    "updatedAt": datetime.utcnow(),
                }
            },
            upsert=True,
        )
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many incorrect attempts. Locked for "
                f"{settings.OTP_VERIFY_LOCK_MINUTES} minutes."
            ),
        )

    await otp_verify_locks_collection.update_one(
        {"key": key},
        {
            "$set": {
                "email": normalized,
                "failedAttempts": failed_attempts,
                "lockedUntil": None,
                "updatedAt": datetime.utcnow(),
            }
        },
        upsert=True,
    )

    remaining = settings.OTP_VERIFY_MAX_ATTEMPTS - failed_attempts
    raise HTTPException(
        status_code=400,
        detail=f"{generic_error}. {remaining} attempt(s) remaining.",
    )
