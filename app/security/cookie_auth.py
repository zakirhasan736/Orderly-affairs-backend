"""HttpOnly cookie helpers and token extraction (cookie-first, Bearer fallback)."""

from fastapi import HTTPException, Request

OWNER_ACCESS_COOKIE = "auth_token"
OWNER_REFRESH_COOKIE = "oa_refresh_token"
NOK_ACCESS_COOKIE = "nok_auth_token"
NOK_REFRESH_COOKIE = "oa_nok_refresh_token"


def cookie_secure() -> bool:
    from app.config import settings

    return settings.APP_ENV != "development"


def set_auth_cookie(
    response,
    *,
    name: str,
    value: str,
    max_age_seconds: int,
) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age_seconds,
        httponly=True,
        secure=cookie_secure(),
        samesite="strict",
        path="/",
    )


def clear_auth_cookies(response, *, owner: bool = True, nok: bool = False) -> None:
    names: list[str] = []
    if owner:
        names.extend([OWNER_ACCESS_COOKIE, OWNER_REFRESH_COOKIE])
    if nok:
        names.extend([NOK_ACCESS_COOKIE, NOK_REFRESH_COOKIE])

    for name in names:
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
