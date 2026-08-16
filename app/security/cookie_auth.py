"""HttpOnly cookie helpers and token extraction (cookie-first, Bearer fallback)."""

from fastapi import HTTPException, Request
from urllib.parse import urlparse

OWNER_ACCESS_COOKIE = "auth_token"
OWNER_REFRESH_COOKIE = "oa_refresh_token"
NOK_ACCESS_COOKIE = "nok_auth_token"
NOK_REFRESH_COOKIE = "oa_nok_refresh_token"
ADMIN_ACCESS_COOKIE = "oa_admin_auth_token"
ADMIN_REFRESH_COOKIE = "oa_admin_refresh_token"


def cookie_secure() -> bool:
    from app.config import settings

    return not settings.is_development


def cookie_domain() -> str | None:
    """Share auth cookies across portal + API subdomains in production.

    Without Domain=, Set-Cookie is host-only on api.* so the Next.js portal
    middleware never sees auth_token and redirects /dashboard → login after
    a successful verify-email / login.
    """
    from app.config import settings

    explicit = (getattr(settings, "COOKIE_DOMAIN", None) or "").strip()
    if explicit:
        return explicit

    if settings.is_development:
        return None

    try:
        host = urlparse(settings.FRONTEND_URL).hostname or ""
    except Exception:
        host = ""

    # vault.orderly-affairs.com → .orderly-affairs.com
    parts = host.split(".")
    if len(parts) >= 2:
        return "." + ".".join(parts[-2:])
    return None


def cookie_samesite() -> str:
    # Lax works across portal ↔ api same registrable domain and top-level nav
    return "lax"


def set_auth_cookie(
    response,
    *,
    name: str,
    value: str,
    max_age_seconds: int,
) -> None:
    kwargs: dict = {
        "key": name,
        "value": value,
        "max_age": max_age_seconds,
        "httponly": True,
        "secure": cookie_secure(),
        "samesite": cookie_samesite(),
        "path": "/",
    }
    domain = cookie_domain()
    if domain:
        kwargs["domain"] = domain
    response.set_cookie(**kwargs)


def clear_auth_cookies(
    response,
    *,
    owner: bool = True,
    nok: bool = False,
    admin: bool = False,
) -> None:
    names: list[str] = []
    if owner:
        names.extend([OWNER_ACCESS_COOKIE, OWNER_REFRESH_COOKIE])
    if nok:
        names.extend([NOK_ACCESS_COOKIE, NOK_REFRESH_COOKIE])
    if admin:
        names.extend([ADMIN_ACCESS_COOKIE, ADMIN_REFRESH_COOKIE])

    domain = cookie_domain()
    for name in names:
        if domain:
            response.delete_cookie(key=name, path="/", domain=domain)
        response.delete_cookie(key=name, path="/")


def extract_bearer_from_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def extract_access_token(
    request: Request,
    authorization: str | None = None,
    *,
    access_cookie: str = OWNER_ACCESS_COOKIE,
    required: bool = True,
) -> str | None:
    cookie_token = request.cookies.get(access_cookie)
    if cookie_token:
        return cookie_token

    header_token = extract_bearer_from_header(authorization)
    if header_token:
        return header_token

    if required:
        raise HTTPException(status_code=401, detail="Missing authorization token")
    return None


def require_owner_access_token(
    request: Request,
    authorization: str | None = None,
) -> str:
    return extract_access_token(
        request,
        authorization,
        access_cookie=OWNER_ACCESS_COOKIE,
        required=True,
    )


def require_nok_access_token(
    request: Request,
    authorization: str | None = None,
) -> str:
    return extract_access_token(
        request,
        authorization,
        access_cookie=NOK_ACCESS_COOKIE,
        required=True,
    )


def require_admin_access_token(
    request: Request,
    authorization: str | None = None,
) -> str:
    return extract_access_token(
        request,
        authorization,
        access_cookie=ADMIN_ACCESS_COOKIE,
        required=True,
    )
