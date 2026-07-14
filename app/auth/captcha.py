import json
import urllib.error
import urllib.parse
import urllib.request

from app.config import settings


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

    if settings.APP_ENV == "development" and token == "dev-bypass":
        return True

    secret = settings.TURNSTILE_SECRET_KEY
    if not secret:
        if settings.APP_ENV == "development":
            return True
        print("⚠️ TURNSTILE_SECRET_KEY missing in production — rejecting CAPTCHA")
        return False

    if not token or not token.strip():
        return False

    payload = urllib.parse.urlencode(
        {
            "secret": secret,
            "response": token.strip(),
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
            if not body.get("success"):
                print(
                    "⚠️ CAPTCHA siteverify rejected:",
                    body.get("error-codes") or body,
                )
            return bool(body.get("success"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"⚠️ CAPTCHA verification failed: {exc}")
        return False
