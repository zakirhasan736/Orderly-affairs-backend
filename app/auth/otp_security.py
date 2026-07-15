from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from random import randint
from typing import Any

import phonenumbers
from fastapi import HTTPException, Request
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.auth.captcha import verify_captcha_token
from app.auth.twilio_verify import send_verification_code
from app.config import settings
from app.database import otp_fraud_logs_collection, otp_verify_locks_collection

# Persist OTP before delivery; rollback if the email fails to send.
EmailOtpStore = Callable[[str, int, datetime], Awaitable[None]]
EmailOtpRollback = Callable[[str], Awaitable[None]]

OTP_SEND_STATUS = ("sent", "blocked", "failed")
OTP_VERIFY_STATUS = ("verified", "failed", "blocked")


def get_client_ip(request: Request) -> str:
    # Prefer Cloudflare's real-client header when present
    cf_ip = (request.headers.get("CF-Connecting-IP") or "").strip()
    if cf_ip:
        return cf_ip

    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def get_user_agent(request: Request) -> str:
    return (request.headers.get("User-Agent") or "")[:512]


def get_session_id(request: Request, explicit_session_id: str | None) -> str:
    header_session = request.headers.get("X-Otp-Session-Id")
    session_id = (explicit_session_id or header_session or "").strip()
    return session_id or "anonymous"


def detect_phone_country(phone_e164: str) -> str:
    try:
        parsed = phonenumbers.parse(phone_e164, None)
        region = phonenumbers.region_code_for_number(parsed)
        return region or "UNKNOWN"
    except phonenumbers.NumberParseException:
        return "UNKNOWN"


def get_allowed_countries() -> set[str]:
    raw = (settings.OTP_ALLOWED_COUNTRIES or "").strip()
    if not raw or raw == "*":
        return set()
    return {code.strip().upper() for code in raw.split(",") if code.strip()}


def ensure_country_allowed(phone_e164: str) -> str:
    country = detect_phone_country(phone_e164)
    allowed = get_allowed_countries()
    if allowed and country not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"SMS verification is not available for phone numbers in {country}.",
        )
    return country


async def log_otp_event(
    *,
    channel: str,
    ip: str,
    user_agent: str,
    session_id: str,
    captcha_passed: bool,
    status: str,
    action: str,
    phone: str = "",
    country: str = "",
    email: str | None = None,
    detail: str | None = None,
    twilio_sid: str | None = None,
) -> None:
    await otp_fraud_logs_collection.insert_one(
        {
            "channel": channel,
            "phone": phone,
            "country": country,
            "ip": ip,
            "userAgent": user_agent,
            "sessionId": session_id,
            "email": (email or "").lower() if email else None,
            "captchaPassed": captcha_passed,
            "status": status,
            "action": action,
            "detail": detail,
            "twilioSid": twilio_sid,
            "createdAt": datetime.utcnow(),
        }
    )


def _as_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def _raise_otp_rate_limit(*, detail: str, retry_after: int, max_wait: int | None = None) -> None:
    """429 with a parseable message and Retry-After for the portal countdown."""
    wait = max(int(retry_after), 1)
    if max_wait is not None:
        wait = min(wait, max_wait)
    raise HTTPException(
        status_code=429,
        detail=f"{detail} Try again in {wait} seconds.",
        headers={"Retry-After": str(wait)},
    )


async def _seconds_until_oldest_send_slot(
    *,
    field: str,
    value: str,
    since: datetime,
    window_seconds: int,
    channel: str | None = None,
) -> int:
    now = datetime.utcnow()
    query: dict[str, Any] = {
        field: value,
        "action": "send",
        "status": "sent",
        "createdAt": {"$gte": since, "$lte": now},
    }
    if channel == "sms":
        query["channel"] = {"$in": ["sms", None]}
    elif channel:
        query["channel"] = channel

    oldest = await otp_fraud_logs_collection.find_one(
        query,
        sort=[("createdAt", 1)],
    )
    if not oldest:
        return min(max(window_seconds, 1), window_seconds)

    created = _as_naive_utc(oldest.get("createdAt"))
    if created is None or created > now:
        # Corrupt / future timestamps must not produce multi-day waits
        return min(max(window_seconds, 1), 600)

    expires_at = created + timedelta(seconds=window_seconds)
    remaining = int((expires_at - now).total_seconds())
    return min(max(remaining, 1), window_seconds)


async def _count_recent_sends(
    *,
    field: str,
    value: str,
    since: datetime,
    channel: str | None = None,
) -> int:
    query: dict[str, Any] = {
        field: value,
        "action": "send",
        "status": "sent",
        "createdAt": {"$gte": since},
    }
    if channel == "sms":
        query["channel"] = {"$in": ["sms", None]}
    elif channel:
        query["channel"] = channel
    return await otp_fraud_logs_collection.count_documents(query)


async def _seconds_since_last_send(phone: str) -> int | None:
    recent = await otp_fraud_logs_collection.find_one(
        {
            "channel": {"$in": ["sms", None]},
            "phone": phone,
            "action": "send",
            "status": "sent",
        },
        sort=[("createdAt", -1)],
    )
    if not recent:
        return None

    elapsed = (datetime.utcnow() - recent["createdAt"]).total_seconds()
    remaining = settings.OTP_PHONE_COOLDOWN_SECONDS - int(elapsed)
    return remaining if remaining > 0 else None


async def enforce_otp_send_limits(
    *,
    request: Request,
    phone: str,
    email: str | None,
    captcha_token: str | None,
    session_id: str | None,
    skip_captcha: bool = False,
) -> dict[str, Any]:
    ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    resolved_session = get_session_id(request, session_id)
    country = ensure_country_allowed(phone)

    captcha_passed = skip_captcha or verify_captcha_token(captcha_token, ip)
    if not captcha_passed:
        await log_otp_event(
            channel="sms",
            phone=phone,
            country=country,
            ip=ip,
            user_agent=user_agent,
            session_id=resolved_session,
            email=email,
            captcha_passed=False,
            status="blocked",
            action="send",
            detail="captcha_failed",
        )
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed")

    cooldown_remaining = await _seconds_since_last_send(phone)
    if cooldown_remaining:
        await log_otp_event(
            channel="sms",
            phone=phone,
            country=country,
            ip=ip,
            user_agent=user_agent,
            session_id=resolved_session,
            email=email,
            captcha_passed=True,
            status="blocked",
            action="send",
            detail=f"phone_cooldown_{cooldown_remaining}s",
        )
        raise HTTPException(
            status_code=429,
            detail=f"Please try again in {cooldown_remaining} seconds.",
            headers={"Retry-After": str(cooldown_remaining)},
        )

    now = datetime.utcnow()
    phone_hour = await _count_recent_sends(
        field="phone",
        value=phone,
        since=now - timedelta(hours=1),
        channel="sms",
    )
    if phone_hour >= settings.OTP_PHONE_MAX_PER_HOUR:
        await log_otp_event(
            channel="sms",
            phone=phone,
            country=country,
            ip=ip,
            user_agent=user_agent,
            session_id=resolved_session,
            email=email,
            captcha_passed=True,
            status="blocked",
            action="send",
            detail="phone_hourly_limit",
        )
        raise HTTPException(
            status_code=429,
            detail="Too many OTP requests for this phone number. Try again later.",
        )

    phone_day = await _count_recent_sends(
        field="phone",
        value=phone,
        since=now - timedelta(days=1),
        channel="sms",
    )
    if phone_day >= settings.OTP_PHONE_MAX_PER_DAY:
        await log_otp_event(
            channel="sms",
            phone=phone,
            country=country,
            ip=ip,
            user_agent=user_agent,
            session_id=resolved_session,
            email=email,
            captcha_passed=True,
            status="blocked",
            action="send",
            detail="phone_daily_limit",
        )
        raise HTTPException(
            status_code=429,
            detail="Daily OTP limit reached for this phone number.",
        )

    ip_hour = await _count_recent_sends(
        field="ip",
        value=ip,
        since=now - timedelta(hours=1),
    )
    if ip_hour >= settings.OTP_IP_MAX_PER_HOUR:
        await log_otp_event(
            channel="sms",
            phone=phone,
            country=country,
            ip=ip,
            user_agent=user_agent,
            session_id=resolved_session,
            email=email,
            captcha_passed=True,
            status="blocked",
            action="send",
            detail="ip_hourly_limit",
        )
        raise HTTPException(
            status_code=429,
            detail="Too many OTP requests from this network. Try again later.",
        )

    ip_day = await _count_recent_sends(
        field="ip",
        value=ip,
        since=now - timedelta(days=1),
    )
    if ip_day >= settings.OTP_IP_MAX_PER_DAY:
        await log_otp_event(
            channel="sms",
            phone=phone,
            country=country,
            ip=ip,
            user_agent=user_agent,
            session_id=resolved_session,
            email=email,
            captcha_passed=True,
            status="blocked",
            action="send",
            detail="ip_daily_limit",
        )
        raise HTTPException(
            status_code=429,
            detail="Daily OTP limit reached from this network.",
        )

    if resolved_session != "anonymous":
        session_hour = await _count_recent_sends(
            field="sessionId",
            value=resolved_session,
            since=now - timedelta(hours=1),
        )
        if session_hour >= settings.OTP_SESSION_MAX_PER_HOUR:
            await log_otp_event(
                phone=phone,
                country=country,
                ip=ip,
                user_agent=user_agent,
                session_id=resolved_session,
                email=email,
                captcha_passed=True,
                status="blocked",
                action="send",
                detail="session_hourly_limit",
            )
            raise HTTPException(
                status_code=429,
                detail="Too many OTP requests from this device. Try again later.",
            )

    return {
        "ip": ip,
        "user_agent": user_agent,
        "session_id": resolved_session,
        "country": country,
        "captcha_passed": captcha_passed,
    }


async def send_otp_sms_secure(
    *,
    request: Request,
    phone: str,
    email: str | None,
    captcha_token: str | None,
    session_id: str | None,
    skip_captcha: bool = False,
) -> dict[str, Any]:
    context = await enforce_otp_send_limits(
        request=request,
        phone=phone,
        email=email,
        captcha_token=captcha_token,
        session_id=session_id,
        skip_captcha=skip_captcha,
    )

    try:
        verification = send_verification_code(phone)
        twilio_sid = getattr(verification, "sid", None)
    except Exception as exc:
        await log_otp_event(
            channel="sms",
            phone=phone,
            country=context["country"],
            ip=context["ip"],
            user_agent=context["user_agent"],
            session_id=context["session_id"],
            email=email,
            captcha_passed=context["captcha_passed"],
            status="failed",
            action="send",
            detail=str(exc),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        await log_otp_event(
            channel="sms",
            phone=phone,
            country=context["country"],
            ip=context["ip"],
            user_agent=context["user_agent"],
            session_id=context["session_id"],
            email=email,
            captcha_passed=context["captcha_passed"],
            status="sent",
            action="send",
            twilio_sid=twilio_sid,
        )
    except Exception as log_exc:
        # SMS already delivered — do not fail the API after a successful send
        print(f"SMS OTP fraud log failed for {phone}: {log_exc}")

    return {
        "phone": phone,
        "cooldown_seconds": settings.OTP_PHONE_COOLDOWN_SECONDS,
        "twilio_sid": twilio_sid,
    }


def _verify_lock_key(phone: str, email: str | None) -> str:
    return f"{phone}|{(email or '').lower()}"


async def ensure_verify_not_locked(phone: str, email: str | None) -> None:
    lock = await otp_verify_locks_collection.find_one(
        {"key": _verify_lock_key(phone, email)}
    )
    if not lock:
        return

    locked_until = lock.get("lockedUntil")
    if locked_until and locked_until > datetime.utcnow():
        remaining = int((locked_until - datetime.utcnow()).total_seconds())
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many incorrect OTP attempts. Try again in "
                f"{max(remaining, 1)} seconds."
            ),
            headers={"Retry-After": str(max(remaining, 1))},
        )

    if locked_until and locked_until <= datetime.utcnow():
        await otp_verify_locks_collection.delete_one({"key": _verify_lock_key(phone, email)})


async def record_verify_attempt(
    *,
    request: Request,
    phone: str,
    email: str | None,
    success: bool,
    session_id: str | None = None,
    twilio_status: str | None = None,
) -> None:
    ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    resolved_session = get_session_id(request, session_id)
    country = detect_phone_country(phone)
    key = _verify_lock_key(phone, email)

    await log_otp_event(
        channel="sms",
        phone=phone,
        country=country,
        ip=ip,
        user_agent=user_agent,
        session_id=resolved_session,
        email=email,
        captcha_passed=True,
        status="verified" if success else "failed",
        action="verify",
        detail=twilio_status,
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
                    "phone": phone,
                    "email": email,
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
                f"Too many incorrect OTP attempts. Locked for "
                f"{settings.OTP_VERIFY_LOCK_MINUTES} minutes."
            ),
        )

    await otp_verify_locks_collection.update_one(
        {"key": key},
        {
            "$set": {
                "phone": phone,
                "email": email,
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
        detail=f"Invalid OTP. {remaining} attempt(s) remaining.",
    )


async def clear_verify_attempts(phone: str, email: str | None) -> None:
    await otp_verify_locks_collection.delete_one(
        {"key": _verify_lock_key(phone, email)}
    )


def _normalize_email(email: str) -> str:
    return email.lower().strip()


def deliver_email_otp(email: str, otp: int) -> None:
    sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=email,
        subject="Your Orderly Affairs verification code",
        html_content=(
            f"<p>Your verification code is <b>{otp}</b>.</p>"
            "<p>It expires in 10 minutes.</p>"
        ),
    )
    sg.send(message)


async def _seconds_since_last_email_send(email: str) -> int | None:
    normalized = _normalize_email(email)
    recent = await otp_fraud_logs_collection.find_one(
        {
            "channel": "email",
            "email": normalized,
            "action": "send",
            "status": "sent",
        },
        sort=[("createdAt", -1)],
    )
    if not recent:
        return None

    created = recent.get("createdAt")
    if created is None:
        return None
    if getattr(created, "tzinfo", None) is not None:
        created = created.replace(tzinfo=None)

    elapsed = (datetime.utcnow() - created).total_seconds()
    remaining = settings.OTP_EMAIL_COOLDOWN_SECONDS - int(elapsed)
    return remaining if remaining > 0 else None


async def enforce_email_otp_send_limits(
    *,
    request: Request,
    email: str,
    captcha_token: str | None,
    session_id: str | None,
    skip_captcha: bool = False,
) -> dict[str, Any]:
    normalized = _normalize_email(email)
    ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    resolved_session = get_session_id(request, session_id)

    captcha_passed = skip_captcha or verify_captcha_token(captcha_token, ip)
    if not captcha_passed:
        await log_otp_event(
            channel="email",
            email=normalized,
            ip=ip,
            user_agent=user_agent,
            session_id=resolved_session,
            captcha_passed=False,
            status="blocked",
            action="send",
            detail="captcha_failed",
        )
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed")

    cooldown_remaining = await _seconds_since_last_email_send(normalized)
    if cooldown_remaining:
        await log_otp_event(
            channel="email",
            email=normalized,
            ip=ip,
            user_agent=user_agent,
            session_id=resolved_session,
            captcha_passed=True,
            status="blocked",
            action="send",
            detail=f"email_cooldown_{cooldown_remaining}s",
        )
        raise HTTPException(
            status_code=429,
            detail=f"Please try again in {cooldown_remaining} seconds.",
            headers={"Retry-After": str(cooldown_remaining)},
        )

    now = datetime.utcnow()
    burst_minutes = settings.OTP_BURST_WINDOW_MINUTES
    burst_since = now - timedelta(minutes=burst_minutes)
    burst_window_seconds = burst_minutes * 60

    email_burst = await _count_recent_sends(
        field="email",
        value=normalized,
        since=burst_since,
        channel="email",
    )
    if email_burst >= settings.OTP_EMAIL_MAX_PER_BURST:
        await log_otp_event(
            channel="email",
            email=normalized,
            ip=ip,
            user_agent=user_agent,
            session_id=resolved_session,
            captcha_passed=True,
            status="blocked",
            action="send",
            detail="email_burst_limit",
        )
        retry = await _seconds_until_oldest_send_slot(
            field="email",
            value=normalized,
            since=burst_since,
            window_seconds=burst_window_seconds,
            channel="email",
        )
        _raise_otp_rate_limit(
            detail="Too many code requests for this email.",
            retry_after=retry,
        )

    hour_since = now - timedelta(hours=1)
    email_hour = await _count_recent_sends(
        field="email",
        value=normalized,
        since=hour_since,
        channel="email",
    )
    if email_hour >= settings.OTP_EMAIL_MAX_PER_HOUR:
        await log_otp_event(
            channel="email",
            email=normalized,
            ip=ip,
            user_agent=user_agent,
            session_id=resolved_session,
            captcha_passed=True,
            status="blocked",
            action="send",
            detail="email_hourly_limit",
        )
        retry = await _seconds_until_oldest_send_slot(
            field="email",
            value=normalized,
            since=hour_since,
            window_seconds=3600,
            channel="email",
        )
        _raise_otp_rate_limit(
            detail="Too many code requests for this email.",
            retry_after=retry,
        )

    day_since = now - timedelta(days=1)
    email_day = await _count_recent_sends(
        field="email",
        value=normalized,
        since=day_since,
        channel="email",
    )
    if email_day >= settings.OTP_EMAIL_MAX_PER_DAY:
        await log_otp_event(
            channel="email",
            email=normalized,
            ip=ip,
            user_agent=user_agent,
            session_id=resolved_session,
            captcha_passed=True,
            status="blocked",
            action="send",
            detail="email_daily_limit",
        )
        retry = await _seconds_until_oldest_send_slot(
            field="email",
            value=normalized,
            since=day_since,
            window_seconds=86400,
            channel="email",
        )
        _raise_otp_rate_limit(
            detail="Daily code limit reached for this email.",
            retry_after=retry,
        )

    ip_hour = await _count_recent_sends(
        field="ip",
        value=ip,
        since=hour_since,
    )
    if ip_hour >= settings.OTP_IP_MAX_PER_HOUR:
        await log_otp_event(
            channel="email",
            email=normalized,
            ip=ip,
            user_agent=user_agent,
            session_id=resolved_session,
            captcha_passed=True,
            status="blocked",
            action="send",
            detail="ip_hourly_limit",
        )
        retry = await _seconds_until_oldest_send_slot(
            field="ip",
            value=ip,
            since=hour_since,
            window_seconds=3600,
        )
        _raise_otp_rate_limit(
            detail="Too many code requests from this network.",
            retry_after=retry,
        )

    ip_day = await _count_recent_sends(
        field="ip",
        value=ip,
        since=day_since,
    )
    if ip_day >= settings.OTP_IP_MAX_PER_DAY:
        await log_otp_event(
            channel="email",
            email=normalized,
            ip=ip,
            user_agent=user_agent,
            session_id=resolved_session,
            captcha_passed=True,
            status="blocked",
            action="send",
            detail="ip_daily_limit",
        )
        retry = await _seconds_until_oldest_send_slot(
            field="ip",
            value=ip,
            since=day_since,
            window_seconds=86400,
        )
        _raise_otp_rate_limit(
            detail="Daily code limit reached from this network.",
            retry_after=retry,
        )

    if resolved_session != "anonymous":
        session_hour = await _count_recent_sends(
            field="sessionId",
            value=resolved_session,
            since=hour_since,
        )
        if session_hour >= settings.OTP_SESSION_MAX_PER_HOUR:
            await log_otp_event(
                channel="email",
                email=normalized,
                ip=ip,
                user_agent=user_agent,
                session_id=resolved_session,
                captcha_passed=True,
                status="blocked",
                action="send",
                detail="session_hourly_limit",
            )
            retry = await _seconds_until_oldest_send_slot(
                field="sessionId",
                value=resolved_session,
                since=hour_since,
                window_seconds=3600,
            )
            _raise_otp_rate_limit(
                detail="Too many code requests from this device.",
                retry_after=retry,
            )

    return {
        "ip": ip,
        "user_agent": user_agent,
        "session_id": resolved_session,
        "email": normalized,
        "captcha_passed": captcha_passed,
    }


async def send_email_otp_secure(
    *,
    request: Request,
    email: str,
    captcha_token: str | None,
    session_id: str | None,
    skip_captcha: bool = False,
    store_otp: EmailOtpStore | None = None,
    rollback_otp: EmailOtpRollback | None = None,
) -> dict[str, Any]:
    """
    Rate-limit → generate → persist (optional) → send.

    The email is delivered only after store_otp succeeds. If SendGrid fails,
    rollback_otp runs and the client gets an error (no success response).
    """
    context = await enforce_email_otp_send_limits(
        request=request,
        email=email,
        captcha_token=captcha_token,
        session_id=session_id,
        skip_captcha=skip_captcha,
    )
    normalized = context["email"]
    otp = randint(100000, 999999)
    expiry = datetime.utcnow() + timedelta(minutes=10)

    # Persist before send so a failed DB write never leaves an emailed code
    # that the API reported as failed — and so send never happens if store fails.
    if store_otp is not None:
        await store_otp(normalized, otp, expiry)

    try:
        deliver_email_otp(normalized, otp)
    except Exception as exc:
        if rollback_otp is not None:
            try:
                await rollback_otp(normalized)
            except Exception as rollback_exc:
                print(f"email OTP rollback failed for {normalized}: {rollback_exc}")

        await log_otp_event(
            channel="email",
            email=normalized,
            ip=context["ip"],
            user_agent=context["user_agent"],
            session_id=context["session_id"],
            captcha_passed=context["captcha_passed"],
            status="failed",
            action="send",
            detail=str(exc),
        )
        raise HTTPException(
            status_code=400,
            detail=f"Failed to send verification email: {exc}",
        ) from exc

    try:
        await log_otp_event(
            channel="email",
            email=normalized,
            ip=context["ip"],
            user_agent=context["user_agent"],
            session_id=context["session_id"],
            captcha_passed=context["captcha_passed"],
            status="sent",
            action="send",
        )
    except Exception as log_exc:
        # Email already delivered — do not fail the API after a successful send
        print(f"email OTP fraud log failed for {normalized}: {log_exc}")

    return {
        "email": normalized,
        "otp": otp,
        "expiry": expiry,
        "cooldown_seconds": settings.OTP_EMAIL_COOLDOWN_SECONDS,
    }


def _email_verify_lock_key(email: str) -> str:
    return f"email|{_normalize_email(email)}"


async def ensure_email_verify_not_locked(email: str) -> None:
    key = _email_verify_lock_key(email)
    lock = await otp_verify_locks_collection.find_one({"key": key})
    if not lock:
        return

    locked_until = lock.get("lockedUntil")
    if locked_until and locked_until > datetime.utcnow():
        remaining = int((locked_until - datetime.utcnow()).total_seconds())
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many incorrect OTP attempts. Try again in "
                f"{max(remaining, 1)} seconds."
            ),
            headers={"Retry-After": str(max(remaining, 1))},
        )

    if locked_until and locked_until <= datetime.utcnow():
        await otp_verify_locks_collection.delete_one({"key": key})


async def record_email_verify_attempt(
    *,
    request: Request,
    email: str,
    success: bool,
    session_id: str | None = None,
) -> None:
    normalized = _normalize_email(email)
    ip = get_client_ip(request)
    user_agent = get_user_agent(request)
    resolved_session = get_session_id(request, session_id)
    key = _email_verify_lock_key(normalized)

    await log_otp_event(
        channel="email",
        email=normalized,
        ip=ip,
        user_agent=user_agent,
        session_id=resolved_session,
        captcha_passed=True,
        status="verified" if success else "failed",
        action="verify",
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
                f"Too many incorrect OTP attempts. Locked for "
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
        detail=f"Invalid OTP. {remaining} attempt(s) remaining.",
    )
