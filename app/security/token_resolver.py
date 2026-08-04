"""Shared access-token verification from HttpOnly cookies or Bearer header."""

from fastapi import Header, HTTPException, Request

from app.security.cookie_auth import (
    ADMIN_ACCESS_COOKIE,
    NOK_ACCESS_COOKIE,
    OWNER_ACCESS_COOKIE,
    extract_access_token,
)
from app.security.jwt_handler import verify_token


def decode_access_token(
    request: Request,
    authorization: str | None = None,
    *,
    access_cookie: str = OWNER_ACCESS_COOKIE,
) -> dict:
    token = extract_access_token(
        request,
        authorization,
        access_cookie=access_cookie,
        required=True,
    )
    decoded = verify_token(token)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return decoded


def decode_admin_token(
    request: Request,
    authorization: str | None = None,
) -> dict:
    """Prefer the isolated admin cookie; Bearer fallback still allowed."""
    return decode_access_token(
        request,
        authorization,
        access_cookie=ADMIN_ACCESS_COOKIE,
    )


def decode_owner_or_nok_token(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    owner_token = request.cookies.get(OWNER_ACCESS_COOKIE)
    nok_token = request.cookies.get(NOK_ACCESS_COOKIE)

    if owner_token:
        decoded = verify_token(owner_token)
        if decoded:
            return decoded

    if nok_token:
        decoded = verify_token(nok_token)
        if decoded:
            return decoded

    header_token = extract_access_token(
        request,
        authorization,
        access_cookie=OWNER_ACCESS_COOKIE,
        required=False,
    )
    if not header_token:
        header_token = extract_access_token(
            request,
            authorization,
            access_cookie=NOK_ACCESS_COOKIE,
            required=False,
        )

    if not header_token:
        raise HTTPException(status_code=401, detail="Missing authorization token")

    decoded = verify_token(header_token)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return decoded
