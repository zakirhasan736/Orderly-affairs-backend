"""Double-submit CSRF for cookie-authenticated mutating requests.

Cookie `oa_csrf_token` is readable by JS when Domain is shared. For cross-origin
dev (localhost:3000 → :8000) the token is also echoed as `X-CSRF-Token` on
responses so the SPA can cache and re-send it.
"""

from __future__ import annotations

import secrets
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.security.cookie_auth import (
    ADMIN_ACCESS_COOKIE,
    ADMIN_REFRESH_COOKIE,
    NOK_ACCESS_COOKIE,
    NOK_REFRESH_COOKIE,
    OWNER_ACCESS_COOKIE,
    OWNER_REFRESH_COOKIE,
    cookie_domain,
    cookie_secure,
)

CSRF_COOKIE = "oa_csrf_token"
CSRF_HEADER = "X-CSRF-Token"

AUTH_COOKIES: tuple[str, ...] = (
    OWNER_ACCESS_COOKIE,
    OWNER_REFRESH_COOKIE,
    NOK_ACCESS_COOKIE,
    NOK_REFRESH_COOKIE,
    ADMIN_ACCESS_COOKIE,
    ADMIN_REFRESH_COOKIE,
)

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Paths that must never require CSRF (external callbacks / health / docs).
EXEMPT_PREFIXES: tuple[str, ...] = (
    "/billing/webhook",
    "/docs",
    "/openapi",
    "/redoc",
    "/health",
)


def _path_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in EXEMPT_PREFIXES)


def _has_auth_cookie(request: Request) -> bool:
    return any(request.cookies.get(name) for name in AUTH_COOKIES)


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _set_csrf_cookie(response: Response, token: str) -> None:
    kwargs: dict = {
        "key": CSRF_COOKIE,
        "value": token,
        "max_age": 60 * 60 * 12,
        "httponly": False,
        "secure": cookie_secure(),
        "samesite": "lax",
        "path": "/",
    }
    domain = cookie_domain()
    if domain:
        kwargs["domain"] = domain
    response.set_cookie(**kwargs)


def clear_csrf_cookie(response: Response) -> None:
    domain = cookie_domain()
    if domain:
        response.delete_cookie(key=CSRF_COOKIE, path="/", domain=domain)
    response.delete_cookie(key=CSRF_COOKIE, path="/")


def csrf_enabled() -> bool:
    from app.config import settings

    return bool(getattr(settings, "CSRF_PROTECTION_ENABLED", True))


class CsrfMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not csrf_enabled():
            return await call_next(request)

        path = request.url.path
        method = request.method.upper()

        if (
            method in MUTATING_METHODS
            and not _path_exempt(path)
            and _has_auth_cookie(request)
        ):
            cookie_token = (request.cookies.get(CSRF_COOKIE) or "").strip()
            header_token = (request.headers.get(CSRF_HEADER) or "").strip()
            if (
                not cookie_token
                or not header_token
                or not secrets.compare_digest(cookie_token, header_token)
            ):
                token = cookie_token or _new_token()
                fail = JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF validation failed"},
                )
                _set_csrf_cookie(fail, token)
                fail.headers[CSRF_HEADER] = token
                return fail

        response = await call_next(request)

        token = (request.cookies.get(CSRF_COOKIE) or "").strip() or _new_token()
        # Always refresh so SPA can learn the token via response header.
        _set_csrf_cookie(response, token)
        response.headers[CSRF_HEADER] = token
        return response
