"""System-owner admin authentication (isolated cookies + dedicated TOTP MFA)."""

from __future__ import annotations

import base64
from datetime import datetime
from io import BytesIO

import pyotp
import qrcode
from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

from app.admin.audit import log_admin_action
from app.admin.deps import is_permitted_admin, resolve_admin_role
from app.admin.permissions import (
    resolve_areas_for_role,
    user_can_clear_rate_limits,
    user_can_delete_users,
    user_can_edit_profile_email,
    user_can_force_logout,
    user_can_manage_roles,
    user_can_suspend_accounts,
    user_is_read_only,
)
from app.auth.session_manager import issue_admin_session, logout_admin_session
from app.database import users_collection
from app.security.auth_rate_limit import enforce_auth_rate_limit, reset_auth_rate_limit
from app.security.jwt_handler import (
    ADMIN_MFA_LOGIN_PURPOSE,
    ADMIN_MFA_SETUP_PURPOSE,
    create_admin_mfa_challenge_token,
    verify_admin_mfa_challenge_token,
    verify_token,
)
from app.security.password_handler import verify_password
from app.security.totp_crypto import (
    encrypt_admin_totp_value,
    read_admin_totp_secret,
    write_admin_totp_fields,
)

admin_auth_router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class AdminMfaSetupStartRequest(BaseModel):
    email: EmailStr
    setup_token: str


class AdminMfaSetupConfirmRequest(BaseModel):
    email: EmailStr
    setup_token: str
    code: str = Field(min_length=6, max_length=8)


class AdminMfaVerifyRequest(BaseModel):
    email: EmailStr
    mfa_challenge_token: str
    code: str = Field(min_length=6, max_length=8)


def _qr_png_base64(otpauth_url: str) -> str:
    qr = qrcode.make(otpauth_url)
    buf = BytesIO()
    qr.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


async def _load_permitted_admin(email: str) -> dict:
    user = await users_collection.find_one({"email": email, "role": "owner"})
    if not user or not is_permitted_admin(user):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    if user.get("suspended") is True:
        raise HTTPException(status_code=403, detail="Admin account suspended")
    return user


@admin_auth_router.post("/login")
async def admin_login(
    payload: AdminLoginRequest,
    request: Request,
    response: Response,
):
    email = payload.email.lower().strip()
    await enforce_auth_rate_limit(request, key=f"admin-login:{email}")

    user = await users_collection.find_one({"email": email, "role": "owner"})
    stored = ""
    if user:
        stored = user.get("password") or user.get("password_hash") or ""

    if (
        not user
        or not is_permitted_admin(user)
        or not verify_password(payload.password, stored)
    ):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")

    await reset_auth_rate_limit(request, key=f"admin-login:{email}")

    # Ensure allowlisted owners get a default admin_role
    if not user.get("admin_role"):
        role = resolve_admin_role(user)
        await users_collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"admin_role": role, "updated_at": datetime.utcnow()}},
        )
        user["admin_role"] = role

    if not user.get("admin_mfa_enabled"):
        setup_token = create_admin_mfa_challenge_token(
            email,
            purpose=ADMIN_MFA_SETUP_PURPOSE,
        )
        await log_admin_action(email, "admin_login_mfa_setup_required")
        return {
            "authenticated": False,
            "mfa_setup_required": True,
            "setup_token": setup_token,
            "email": email,
            "message": "Admin MFA enrollment required",
        }

    challenge = create_admin_mfa_challenge_token(
        email,
        purpose=ADMIN_MFA_LOGIN_PURPOSE,
    )
    await log_admin_action(email, "admin_login_mfa_required")
    return {
        "authenticated": False,
        "mfa_required": True,
        "mfa_challenge_token": challenge,
        "email": email,
        "message": "Admin MFA verification required",
    }


@admin_auth_router.post("/mfa/setup/start")
async def admin_mfa_setup_start(payload: AdminMfaSetupStartRequest):
    email = payload.email.lower().strip()
    if not verify_admin_mfa_challenge_token(
        payload.setup_token,
        email,
        purpose=ADMIN_MFA_SETUP_PURPOSE,
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired setup token")

    user = await _load_permitted_admin(email)
    if user.get("admin_mfa_enabled"):
        raise HTTPException(status_code=400, detail="Admin MFA already enabled")

    secret = pyotp.random_base32()
    enc = encrypt_admin_totp_value(email, secret)
    await users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "admin_provisioned_secret": enc,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    otpauth_url = pyotp.TOTP(secret).provisioning_uri(
        name=email,
        issuer_name="Orderly Affairs Admin",
    )
    qr_b64 = _qr_png_base64(otpauth_url)
    return {
        "otpauth_url": otpauth_url,
        "secret": secret,
        "qr_png_base64": qr_b64,
        "qrCodeUrl": f"data:image/png;base64,{qr_b64}",
    }


@admin_auth_router.post("/mfa/setup/confirm")
async def admin_mfa_setup_confirm(
    payload: AdminMfaSetupConfirmRequest,
    response: Response,
):
    email = payload.email.lower().strip()
    if not verify_admin_mfa_challenge_token(
        payload.setup_token,
        email,
        purpose=ADMIN_MFA_SETUP_PURPOSE,
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired setup token")

    user = await _load_permitted_admin(email)
    if user.get("admin_mfa_enabled"):
        raise HTTPException(status_code=400, detail="Admin MFA already enabled")

    provisioned = user.get("admin_provisioned_secret")
    if not provisioned:
        raise HTTPException(status_code=400, detail="Start MFA setup first")

    from app.security.totp_crypto import decrypt_admin_totp_value

    secret = decrypt_admin_totp_value(email, provisioned)
    if not secret or not pyotp.TOTP(secret).verify(payload.code.strip(), valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid authenticator code")

    fields = write_admin_totp_fields(email, secret)
    await users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                **fields,
                "admin_mfa_enabled": True,
                "updated_at": datetime.utcnow(),
            },
            "$unset": {"admin_provisioned_secret": ""},
        },
    )

    updated = await users_collection.find_one({"_id": user["_id"]})
    await log_admin_action(email, "admin_mfa_enrolled")
    session = await issue_admin_session(response, updated or user)
    return session


@admin_auth_router.post("/mfa/verify")
async def admin_mfa_verify(
    payload: AdminMfaVerifyRequest,
    request: Request,
    response: Response,
):
    email = payload.email.lower().strip()
    await enforce_auth_rate_limit(request, key=f"admin-mfa:{email}")

    if not verify_admin_mfa_challenge_token(
        payload.mfa_challenge_token,
        email,
        purpose=ADMIN_MFA_LOGIN_PURPOSE,
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired MFA token")

    user = await _load_permitted_admin(email)
    if not user.get("admin_mfa_enabled"):
        raise HTTPException(status_code=400, detail="Admin MFA not enabled")

    secret = read_admin_totp_secret(user)
    if not secret or not pyotp.TOTP(secret).verify(payload.code.strip(), valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid authenticator code")

    await reset_auth_rate_limit(request, key=f"admin-mfa:{email}")
    await users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"admin_last_login": datetime.utcnow(), "updated_at": datetime.utcnow()}},
    )
    await log_admin_action(email, "admin_login_success")
    return await issue_admin_session(response, user)


@admin_auth_router.get("/session")
async def admin_session(
    request: Request,
    authorization: str | None = Header(default=None),
):
    from app.security.cookie_auth import ADMIN_ACCESS_COOKIE
    from app.security.cookie_auth import extract_bearer_from_header

    admin_cookie = request.cookies.get(ADMIN_ACCESS_COOKIE)
    bearer = extract_bearer_from_header(authorization)
    if not admin_cookie and not bearer:
        raise HTTPException(status_code=401, detail="Admin session required")

    if admin_cookie:
        decoded = verify_token(admin_cookie)
        if not decoded or decoded.get("role") != "admin":
            raise HTTPException(status_code=401, detail="Invalid admin session")
    else:
        decoded = verify_token(bearer or "")
        if not decoded or decoded.get("role") != "admin":
            raise HTTPException(status_code=401, detail="Invalid admin session")

    email = str(decoded.get("sub") or decoded.get("email") or "").strip().lower()
    user = await users_collection.find_one({"email": email, "role": "owner"})
    if not user or not is_permitted_admin(user):
        raise HTTPException(status_code=403, detail="Admin only")

    role = resolve_admin_role(user)
    scoped = {**user, "admin_role": role}
    return {
        "authenticated": True,
        "role": "admin",
        "admin_role": role,
        "admin_areas": resolve_areas_for_role(
            role,
            user.get("admin_areas")
            if isinstance(user.get("admin_areas"), list)
            else None,
        ),
        "email": email,
        "admin_mfa_enabled": bool(user.get("admin_mfa_enabled")),
        "full_name": user.get("full_name") or user.get("name"),
        "name": user.get("full_name") or user.get("name"),
        "can_manage_roles": user_can_manage_roles(scoped),
        "can_edit_profile_email": user_can_edit_profile_email(scoped),
        "can_suspend_accounts": user_can_suspend_accounts(scoped),
        "can_clear_rate_limits": user_can_clear_rate_limits(scoped),
        "can_force_logout": user_can_force_logout(scoped),
        "can_delete_users": user_can_delete_users(scoped),
        "read_only": user_is_read_only(scoped),
    }


@admin_auth_router.post("/logout")
async def admin_logout(request: Request, response: Response):
    result = await logout_admin_session(response, request)
    return result
