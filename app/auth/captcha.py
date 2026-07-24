import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import settings

# Turnstile tokens are single-use at Cloudflare. After a successful siteverify we
# remember the token briefly so a retry (wrong password, double-submit, MFA chain)
# does not fail with timeout-or-duplicate and get misread as bad credentials.
_VERIFIED_TOKENS: dict[str, float] = {}
_VERIFIED_LOCK = threading.Lock()
_VERIFIED_TTL_SECONDS = 5 * 60


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def _prune_verified_locked(now: float) -> None:
    expired = [key for key, expires_at in _VERIFIED_TOKENS.items() if expires_at <= now]
    for key in expired:
        del _VERIFIED_TOKENS[key]


def _remember_verified_token(token: str) -> None:
    with _VERIFIED_LOCK:
        now = time.time()
        _prune_verified_locked(now)
        _VERIFIED_TOKENS[_token_fingerprint(token)] = now + _VERIFIED_TTL_SECONDS


def _was_recently_verified(token: str) -> bool:
    with _VERIFIED_LOCK:
        now = time.time()
        _prune_verified_locked(now)
        expires_at = _VERIFIED_TOKENS.get(_token_fingerprint(token))
        return expires_at is not None and expires_at > now


def verify_captcha_token(token: str | None, remote_ip: str | None = None) -> bool:
    """Verify a Cloudflare Turnstile token.

    ``remote_ip`` is accepted for call-site compatibility but is intentionally
    not sent to siteverify. Behind Cloudflare/nginx the app often sees a proxy
    IP, and a mismatched ``remoteip`` makes Cloudflare reject valid tokens
    (seen as HTTP 400 CAPTCHA failures on password-reset / OTP).
    """
    del remote_ip  # unused on purpose — see docstring

    if not settings.OTP_CAPTCHA_ENABLED:
        return True

    if settings.APP_ENV == "development" and token in ("dev-bypass", "captcha-disabled"):
        return True

    secret = settings.TURNSTILE_SECRET_KEY
    if not secret:
        if settings.APP_ENV == "development":
            return True
        print("⚠️ TURNSTILE_SECRET_KEY missing in production — rejecting CAPTCHA")
        return False

    if not token or not token.strip() or token.strip() == "captcha-disabled":
        # Real Turnstile tokens required when OTP_CAPTCHA_ENABLED=true
        print("⚠️ CAPTCHA token missing or disabled-placeholder while captcha enabled")
        return False

    normalized = token.strip()

    # Already passed siteverify in this short window (retry / parallel request).
    if _was_recently_verified(normalized):
        return True

    payload = urllib.parse.urlencode(
        {
            "secret": secret,
            "response": normalized,
        }
    ).encode()

    request = urllib.request.Request(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
            if body.get("success"):
                _remember_verified_token(normalized)
                return True

            codes = body.get("error-codes") or []
            print("⚠️ CAPTCHA siteverify rejected:", codes or body)

            # Parallel login/signup can consume the token once; accept if our
            # cache already recorded a successful verify from the sibling request.
            if "timeout-or-duplicate" in codes:
                if _was_recently_verified(normalized):
                    return True
                # Sibling may still be writing the cache — brief re-check.
                time.sleep(0.2)
                if _was_recently_verified(normalized):
                    return True
                print(
                    "⚠️ CAPTCHA tip: Turnstile tokens are single-use. "
                    "Retry after a fresh Cloudflare check."
                )
            if "invalid-input-secret" in codes or "missing-input-secret" in codes:
                print("⚠️ CAPTCHA tip: TURNSTILE_SECRET_KEY does not match the site key")
            return False
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"⚠️ CAPTCHA verification failed: {exc}")
        return False
