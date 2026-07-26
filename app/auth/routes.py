from fastapi import APIRouter, Request, HTTPException, Header, Depends, Response
from typing import List, Union

from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
from random import randint
from app.auth.death_detection import record_nextkin_last_login, record_owner_last_login
from app.auth.service import mark_owner_deceased, trigger_death_letters
from bson.errors import InvalidId
from secrets import token_urlsafe
from io import BytesIO
import pyotp, qrcode, base64, random, string, sendgrid
from sendgrid.helpers.mail import Mail
from bson import ObjectId
from passlib.context import CryptContext
from app.security.usage_guard import enforce_usage
from app.auth.phone import (
    ensure_phone_available,
    find_owner_by_login_identifier,
    format_phone,
    looks_like_email,
)
from app.auth.twilio_verify import check_verification_code
from app.auth.otp_security import (
    send_otp_sms_secure,
    send_email_otp_secure,
    ensure_verify_not_locked,
    record_verify_attempt,
    ensure_email_verify_not_locked,
    record_email_verify_attempt,
    get_client_ip,
)

from app.security.otp_storage import hash_otp_value, otp_storage_fields, verify_stored_otp
from app.security.otp_verify_lock import (
    ensure_otp_verify_not_locked,
    record_otp_verify_attempt,
)
from app.database import (
    users_collection,
    otp_collection,
    sms_mfa_attempts_collection,
    pending_signup_collection,
)
from app.security.billing_guard import enforce_billing
from app.security.password_handler import hash_password, verify_password
from app.security.nextkin_profile_crypto import (
    load_nextkin_profile,
    prepare_nextkin_profile_for_storage,
)
from app.security.jwt_handler import (
    create_access_token,
    verify_token,
    create_mfa_challenge_token,
    verify_mfa_challenge_token,
    verify_step_up_token,
    create_step_up_token,
)
from app.security.cookie_auth import (
    OWNER_ACCESS_COOKIE,
    NOK_ACCESS_COOKIE,
    extract_access_token,
)
from app.security.token_resolver import decode_access_token
from app.security.device_fingerprint import log_device_fingerprint
from app.security.totp_crypto import (
    encrypt_totp_value,
    read_pending_totp_secret,
    read_user_provisioned_secret,
    read_user_totp_secret,
)
from app.security.auth_rate_limit import (
    enforce_auth_rate_limit,
    reset_auth_rate_limit,
)
from app.auth.session_manager import (
    issue_owner_session,
    issue_nok_session,
    logout_owner_session,
    logout_nok_session,
    refresh_session_from_cookie,
)
from app.config import settings
from datetime import datetime
from app.notifications.nextkin_emails import (
    send_nextkin_email,
    NextKinEmailEvent,
)
from app.notifications.display_names import (
    resolve_nextkin_display_name,
    resolve_owner_display_name,
)
import string, random

from sendgrid import SendGridAPIClient

router = APIRouter(prefix="/auth", tags=["auth"])

MFA_GENERIC_ERROR = "Unable to complete verification. Please try again."
PENDING_SIGNUP_GENERIC = (
    "If a signup is in progress for that email, you may continue setup."
)
NOK_LOGIN_GENERIC = (
    "Unable to sign in. Contact the kit owner for assistance."
)
PASSWORD_RESET_GENERIC_ERROR = "Unable to reset password. Please try again."
# pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# ============================================================
# MODELS
# ============================================================
class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone_number: str | None = None
    mfa_method: str | None = None  # "sms" | "email" | "authenticator"
    captcha_token: str | None = None
    otp_session_id: str | None = None

    def resolved_full_name(self) -> str | None:
        if self.full_name and self.full_name.strip():
            return self.full_name.strip()
        parts = [
            (self.first_name or "").strip(),
            (self.last_name or "").strip(),
        ]
        joined = " ".join(p for p in parts if p)
        return joined or None

class LoginRequest(BaseModel):
    # Email or phone number (field name kept for API compatibility)
    email: str
    password: str
    captcha_token: str | None = None
    otp_session_id: str | None = None

class VerifyTOTPRequest(BaseModel):
    email: EmailStr
    code: str
    mfa_challenge_token: str | None = None

class EmailRequest(BaseModel):
    email: EmailStr
    captcha_token: str | None = None
    otp_session_id: str | None = None
    mfa_challenge_token: str | None = None
    # "signup" skips Cloudflare — pending signup OTP only
    flow: str | None = None

class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: int
    otp_session_id: str | None = None
    mfa_challenge_token: str | None = None

class LinkAuthenticatorRequest(BaseModel):
    email: EmailStr
    code: str
    secret: str | None = None

class OwnerResetRequest(BaseModel):
    email: EmailStr
    captcha_token: str | None = None
    otp_session_id: str | None = None

class OwnerResetPassword(BaseModel):
    email: EmailStr
    otp: int
    new_password: str
    captcha_token: str | None = None


class StartSMSMFARequest(BaseModel):
    email: EmailStr
    phoneNumber: str | None = None
    captcha_token: str | None = None
    otp_session_id: str | None = None
    mfa_challenge_token: str | None = None


class StartEmailMFARequest(BaseModel):
    email: EmailStr
    captcha_token: str | None = None
    otp_session_id: str | None = None
    mfa_challenge_token: str | None = None


class VerifySMSOTPRequest(BaseModel):
    email: EmailStr
    code: str
    otp_session_id: str | None = None
    mfa_challenge_token: str | None = None


class ResendSignupSMSRequest(BaseModel):
    email: EmailStr
    captcha_token: str | None = None
    otp_session_id: str | None = None
    # "signup" skips Cloudflare for pending-signup SMS resend
    flow: str | None = None

class PhoneRequest(BaseModel):
    phoneNumber: str

class MFAMethodRequest(BaseModel):
    method: str
    password: str | None = None
    mfa_challenge_token: str | None = None
    step_up_token: str | None = None


class ReportOwnerDeceasedRequest(BaseModel):
    master_password: str
    confirm: bool = False

class MFAResetRequest(BaseModel):
    password: str | None = None
    mfa_challenge_token: str | None = None
    step_up_token: str | None = None


class DeleteAccountRequest(BaseModel):
    """Owner must re-enter password and type DELETE to confirm wipe."""
    password: str
    confirm: str
    mfa_challenge_token: str | None = None
    step_up_token: str | None = None


from app.auth.nextkin_schemas import NextKinCreateRequest

# ---- Next-of-Kin ----
class NextKinUpdateRequest(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = None
    relationship: str | None = None
    phone_number: str | None = None
    access_level: str | None = None
    authorized_sections: list[str] | None = None
    immediate_access: bool | None = None
    nok_letter_received: bool | None = None
    master_password: str | None = None
    password_card_generated: bool | None = None
    card_storage_location: str | None = None
    key_bag_location: str | None = None
    documents_bag_location: str | None = None
    special_instructions: str | None = None

class NextKinLoginRequest(BaseModel):
    email: EmailStr
    master_password: str

MFA_METHODS = ("authenticator", "email", "sms")


def normalize_mfa_methods(user: dict) -> dict[str, bool]:
    stored = user.get("mfa_methods") or {}
    methods = {method: bool(stored.get(method)) for method in MFA_METHODS}
    primary = user.get("primary_mfa")

    if primary in methods:
        methods[primary] = True

    return methods


def first_enabled_mfa_method(methods: dict[str, bool]) -> str | None:
    for method in MFA_METHODS:
        if methods.get(method):
            return method
    return None


def mfa_login_response(user: dict, billing: dict) -> dict:
    from app.billing.access import billing_session_flags

    methods = normalize_mfa_methods(user)
    preferred = user.get("primary_mfa")
    if preferred not in methods or not methods.get(preferred):
        preferred = first_enabled_mfa_method(methods)

    flags = billing_session_flags(billing)
    return {
        "message": "Password verified",
        "mfa_required": True,
        "method": preferred,
        "methods": [method for method, enabled in methods.items() if enabled],
        "mfa_methods": methods,
        "email": user["email"],
        "phone": user.get("phone"),
        "billing_status": flags["billing_status"],
        "requires_billing": flags["requires_billing"],
        "is_complimentary": flags["is_complimentary"],
        "otp_sent": False,
    }


async def _store_login_email_otp(email: str, otp: int, expiry: datetime) -> None:
    await otp_collection.delete_many({"email": email})
    doc = otp_storage_fields(email, otp, "login_email")
    doc["expires"] = expiry
    doc["created_at"] = datetime.utcnow()
    await otp_collection.insert_one(doc)


async def _rollback_login_email_otp(email: str) -> None:
    await otp_collection.delete_many({"email": email, "type": "login_email"})


async def _trigger_login_mfa_otp(
    *,
    request: Request,
    user: dict,
    method: str | None,
    email: str,
) -> tuple[bool, int | None, str | None]:
    if method == "email":
        try:
            result = await send_email_otp_secure(
                request=request,
                email=email,
                captcha_token=None,
                session_id=None,
                skip_captcha=True,
                store_otp=_store_login_email_otp,
                rollback_otp=_rollback_login_email_otp,
            )
            return True, result["cooldown_seconds"], None
        except HTTPException as exc:
            return False, None, str(exc.detail)
        except Exception as exc:
            return False, None, str(exc)

    if method == "sms":
        phone = user.get("phone")
        if not phone:
            return False, None, "Phone number not configured"

        try:
            await send_otp_sms_secure(
                request=request,
                phone=phone,
                email=email,
                captcha_token=None,
                session_id=None,
                skip_captcha=True,
            )
            return True, settings.OTP_PHONE_COOLDOWN_SECONDS, None
        except HTTPException as exc:
            return False, None, str(exc.detail)
        except Exception as exc:
            return False, None, str(exc)

    return False, None, None


async def get_authorized_owner_for_email(
    email: str,
    authorization: str | None,
    request: Request | None = None,
) -> dict | None:
    if request is None:
        return None

    try:
        decoded = decode_access_token(request, authorization)
    except HTTPException:
        return None

    owner = await users_collection.find_one({
        "email": decoded["sub"],
        "role": "owner"
    })
    if not owner or owner.get("email") != email:
        return None

    return owner


async def require_login_mfa_proof(
    *,
    email: str,
    mfa_challenge_token: str | None,
    authorization: str | None,
    request: Request,
    pending: dict | None,
) -> None:
    """Signup and settings flows are exempt; login MFA must prove password step."""
    if pending:
        return

    authorized_owner = await get_authorized_owner_for_email(
        email,
        authorization,
        request=request,
    )
    if authorized_owner:
        return

    if not verify_mfa_challenge_token(mfa_challenge_token, email):
        raise HTTPException(
            status_code=403,
            detail="Password verification required before MFA completion",
        )


def require_step_up_auth(
    *,
    user: dict,
    password: str | None,
    mfa_challenge_token: str | None = None,
    step_up_token: str | None = None,
) -> None:
    """Sensitive actions require recent password proof or a short-lived step-up token."""
    email = user["email"]

    if password and verify_password(password, user.get("password", "")):
        return

    if verify_mfa_challenge_token(mfa_challenge_token, email):
        return

    if verify_step_up_token(step_up_token, email):
        return

    raise HTTPException(
        status_code=403,
        detail="Password verification required for this action",
    )

def build_owner_user_document(
    *,
    email: str,
    hashed_password: str,
    full_name: str | None,
    phone: str | None,
    mfa_method: str | None,
    totp_secret: str | None = None,
    mfa_linked: bool = False,
):
    import uuid as _uuid

    return {
        "email": email,
        "password": hashed_password,
        "full_name": full_name,
        "phone": phone,
        "role": "owner",
        "owner_id": None,
        # Opaque vault folder id (not Mongo _id) under VAULT_ROOT/users/
        "folder_uuid": str(_uuid.uuid4()),
        "verified": True,
        "totp_secret": totp_secret,
        "provisioned_secret": None,
        "mfa_linked": mfa_linked,
        "mfa_enabled": mfa_method is not None,
        "primary_mfa": mfa_method,
        "mfa_methods": {
            "email": mfa_method == "email",
            "authenticator": mfa_method == "authenticator",
            "sms": mfa_method == "sms",
        },
        "billing": {
            "customer_id": None,
            "subscription_id": None,
            "status": "pending",
            "plan": None,
            "is_trial": False,
            "trial_start": None,
            "trial_end": None,
            "trial_mode": None,
            "payment_method_attached": False,
            "auto_renew": True,
            "payment_fail_reminders_sent": [],
            "comp": {
                "enabled": False,
                "kind": None,
                "starts_at": None,
                "ends_at": None,
                "granted_by": None,
                "granted_at": None,
                "note": None,
                "reminders_sent": [],
            },
        },
        "enterprise": False,
        "enterprise_limits": {
            "nextkin": None,
            "storage_gb": None,
        },
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }


async def get_active_pending_signup(email: str):
    return await pending_signup_collection.find_one({
        "email": email,
        "expires_at": {"$gt": datetime.utcnow()}
    })


async def delete_expired_pending_signup(email: str):
    await pending_signup_collection.delete_many({
        "email": email,
        "expires_at": {"$lte": datetime.utcnow()}
    })


async def create_real_user_from_pending(pending: dict):
    phone = pending.get("phone")
    if phone:
        await ensure_phone_available(
            phone,
            users_collection=users_collection,
            pending_signup_collection=pending_signup_collection,
            exclude_pending_email=pending.get("email"),
        )

    new_user = build_owner_user_document(
        email=pending["email"],
        hashed_password=pending["password"],
        full_name=pending.get("full_name"),
        phone=phone,
        mfa_method=pending.get("mfa_method"),
        totp_secret=pending.get("totp_secret"),
        mfa_linked=pending.get("mfa_method") == "authenticator",
    )

    await users_collection.insert_one(new_user)
    await pending_signup_collection.delete_one({"_id": pending["_id"]})

    return await users_collection.find_one({
        "email": pending["email"],
        "role": "owner"
    })


async def approve_nextkin_access(
    *,
    nextkin: dict,
    owner: dict,
    plain_password: str | None = None,
):
    if nextkin.get("immediate_access"):
        return

    await users_collection.update_one(
        {"_id": nextkin["_id"]},
        {
            "$set": {
                "immediate_access": True,
                "nok_letter_received": False,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    await send_nextkin_email(
        event=NextKinEmailEvent.ACCESS_APPROVED,
        nextkin=nextkin,
        owner=owner,
        plain_password=plain_password,
    )

# Helper to flip immediate_access and notify the Next-of-Kin
async def _approve_and_notify_if_needed(
    nextkin: dict,
    owner: dict,
    approved: bool = True,
    plain_password: str | None = None,
):
    if bool(nextkin.get("immediate_access", False)) == approved:
        return

    update_fields: dict = {
        "immediate_access": approved,
        "updated_at": datetime.utcnow(),
    }

    if approved:
        update_fields["nok_letter_received"] = False
        update_fields["access_revoked"] = False
    elif nextkin.get("access_timing") == "immediate":
        update_fields["access_revoked"] = True

    await users_collection.update_one(
        {"_id": nextkin["_id"]},
        {"$set": update_fields},
    )

    try:
        if approved:
            await send_nextkin_email(
                event=NextKinEmailEvent.ACCESS_APPROVED,
                nextkin=nextkin,
                owner=owner,
                plain_password=plain_password,
            )
        else:
            await send_nextkin_email(
                event=NextKinEmailEvent.ACCESS_REVOKED,
                nextkin=nextkin,
                owner=owner,
            )
    except Exception as e:
        print("⚠️ Next-of-Kin access notification email failed:", e)

async def notify_owner_nextkin_login(*, owner: dict, nextkin: dict):
    try:
        from app.notifications.email_layout import (
            access_url,
            email_cta_row,
            escape,
            kit_url,
            paper_body,
            render_reminder_card,
        )

        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)

        nk_label = nextkin.get("full_name") or nextkin["email"]
        first = (str(nk_label).strip().split() or ["Someone"])[0]
        access = nextkin.get("access_level") or "Full Kit Access"
        when = datetime.utcnow().strftime("%b %d · %I:%M %p UTC").replace(" 0", " ")
        immediate = bool(nextkin.get("immediate_access"))
        access_note = (
            f"{escape(access)} with immediate access turned on."
            if immediate
            else f"{escape(access)}."
        )

        html = render_reminder_card(
            schedule_label="Event-driven · someone signed in",
            title=f"{first} opened your kit.",
            preheader=f"{nk_label} accessed your Orderly Affairs kit",
            warning=True,
            body_html="".join(
                [
                    paper_body(
                        f"{escape(when)} · {escape(nextkin.get('email') or '')}. "
                        f"They have {access_note}"
                    ),
                    email_cta_row(
                        (kit_url(), "This was expected"),
                        (access_url(), "Revoke access now"),
                        secondary_variant="danger",
                    ),
                ]
            ),
        )

        message = Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=owner["email"],
            subject="Orderly Affairs – Next-of-Kin Access Alert",
            html_content=html,
        )

        sg.send(message)

    except Exception as e:
        # Never block login
        print("Owner login notification failed:", e)

# ============================================================
# 1️⃣ OWNER SIGNUP
# ============================================================

@router.post("/signup")
async def signup(user: SignupRequest, request: Request):
    email = user.email.lower().strip()

    if not user.mfa_method or user.mfa_method not in MFA_METHODS:
        raise HTTPException(
            status_code=400,
            detail="MFA is required. Choose authenticator, email, or sms.",
        )

    await enforce_auth_rate_limit(request, key=f"signup:{email}")

    # real user already exists
    existing_user = await users_collection.find_one({"email": email})
    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Unable to create account. If you already have an account, try signing in.",
        )

    # remove expired pending signups first
    await delete_expired_pending_signup(email)

    # pending signup already exists
    existing_pending = await get_active_pending_signup(email)
    if existing_pending:
        # Same person continuing (password matches) may restart / switch MFA method.
        if not verify_password(user.password, existing_pending.get("password", "")):
            raise HTTPException(
                status_code=400,
                detail="Signup already started. Please complete verification or use resend."
            )
        await pending_signup_collection.delete_one({"_id": existing_pending["_id"]})

    phone = None
    if user.mfa_method == "sms":
        if not user.phone_number or not user.phone_number.strip():
            raise HTTPException(
                status_code=400,
                detail="Phone number required for SMS MFA"
            )
        try:
            phone = format_phone(user.phone_number)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        await ensure_phone_available(
            phone,
            users_collection=users_collection,
            pending_signup_collection=pending_signup_collection,
            exclude_pending_email=email,
        )

    hashed_pw = hash_password(user.password)

    full_name = user.resolved_full_name()
    if not full_name:
        raise HTTPException(
            status_code=400,
            detail="First name and last name are required",
        )

    pending_doc = {
        "email": email,
        "password": hashed_pw,
        "full_name": full_name,
        "phone": phone,
        "mfa_method": user.mfa_method,
        "totp_secret": None,
        "provisioned_secret": None,
        "mfa_linked": False,
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(minutes=15),
    }

    # SMS signup
    if user.mfa_method == "sms":
        await pending_signup_collection.insert_one(pending_doc)

        try:
            await send_otp_sms_secure(
                request=request,
                phone=phone,
                email=email,
                captcha_token=user.captcha_token,
                session_id=user.otp_session_id,
                # Captcha already verified above — Turnstile tokens are single-use
                skip_captcha=True,
            )
        except HTTPException:
            await pending_signup_collection.delete_one({"email": email})
            raise
        except Exception as e:
            await pending_signup_collection.delete_one({"email": email})
            raise HTTPException(status_code=400, detail=str(e))

        return {
            "message": "Signup started. OTP sent to phone.",
            "otp_required": True,
            "method": "sms",
            "email": email,
            "phone": phone,
            "flow": "signup",
            "cooldown_seconds": settings.OTP_PHONE_COOLDOWN_SECONDS,
        }

    # Email signup
    if user.mfa_method == "email":
        pending_doc["email_otp_hash"] = None
        pending_doc["email_otp_expires"] = None

        await pending_signup_collection.insert_one(pending_doc)

        async def _store_signup_email_otp(
            target_email: str, otp: int, expiry: datetime
        ) -> None:
            await pending_signup_collection.update_one(
                {"email": target_email},
                {
                    "$set": {
                        "email_otp_hash": hash_otp_value(
                            target_email, otp, "signup"
                        ),
                        "email_otp_expires": expiry,
                    }
                },
            )

        async def _rollback_signup_email_otp(target_email: str) -> None:
            await pending_signup_collection.delete_one({"email": target_email})

        try:
            email_result = await send_email_otp_secure(
                request=request,
                email=email,
                captcha_token=user.captcha_token,
                session_id=user.otp_session_id,
                # Captcha already verified above — Turnstile tokens are single-use
                skip_captcha=True,
                store_otp=_store_signup_email_otp,
                rollback_otp=_rollback_signup_email_otp,
            )
        except HTTPException:
            await pending_signup_collection.delete_one({"email": email})
            raise
        except Exception as e:
            await pending_signup_collection.delete_one({"email": email})
            raise HTTPException(status_code=400, detail=str(e))

        return {
            "message": "Signup started. OTP sent to email.",
            "otp_required": True,
            "method": "email",
            "email": email,
            "flow": "signup",
            "cooldown_seconds": email_result["cooldown_seconds"],
        }

    # Authenticator signup
    if user.mfa_method == "authenticator":
        secret = pyotp.random_base32()
        pending_doc["provisioned_secret"] = encrypt_totp_value(email, secret, pending=True)

        await pending_signup_collection.insert_one(pending_doc)

        uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name=email,
            issuer_name="Orderly Affairs"
        )
        qr = qrcode.make(uri)
        buf = BytesIO()
        qr.save(buf, format="PNG")
        img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        return {
            "message": "Signup started. Complete authenticator setup.",
            "otp_required": True,
            "method": "authenticator",
            "email": email,
            "qrCodeUrl": f"data:image/png;base64,{img_base64}",
            "flow": "signup"
        }

    raise HTTPException(
        status_code=400,
        detail="MFA is required. Choose authenticator, email, or sms.",
    )

# ============================================================
# 2️⃣ OWNER LOGIN
# ============================================================
# @router.post("/login")
# async def owner_login(data: LoginRequest):
#     email = data.email.lower()
#     user = await users_collection.find_one({"email": email, "role": "owner"})
#     if not user:
#         raise HTTPException(status_code=404, detail="Owner not found")
#     if not verify_password(data.password, user["password"]):
#         raise HTTPException(status_code=400, detail="Invalid credentials")
#     billing = user.get("billing", {})
#     return {
#         "message": "Password verified",
#         "email": email,
#         "role": "owner",
#         "mfa_enabled": user.get("mfa_enabled", False),
#         "primary_mfa": user.get("primary_mfa"),
#         "mfa_methods": user.get("mfa_methods", {}),
#         "billing_status": billing.get("status", "pending"),
#         "requires_billing": billing.get("status") in ["pending", "blocked"]
#     }
# @router.post("/login")
# async def owner_login(data: LoginRequest):
#     email = data.email.lower()

#     user = await users_collection.find_one({
#         "email": email,
#         "role": "owner"
#     })

#     if not user:
#         raise HTTPException(status_code=404, detail="Owner not found")

#     if not verify_password(data.password, user["password"]):
#         raise HTTPException(status_code=400, detail="Invalid credentials")

#     billing = user.get("billing", {})

#     # ============================================================
#     # 🔐 MFA HANDLING (EXTENDED FOR SMS)
#     # ============================================================
#     if user.get("mfa_enabled"):

#         primary_mfa = user.get("primary_mfa")

#         # ========================================================
#         # ✅ SMS MFA (AUTO TRIGGER OTP HERE)
#         # ========================================================
#         if primary_mfa == "sms":

#             phone = user.get("phone")

#             if not phone:
#                 return {
#                     "message": "Phone number required for SMS MFA",
#                     "email": email,
#                     "role": "owner",
#                     "mfa_enabled": True,
#                     "primary_mfa": "sms",
#                     "mfa_methods": user.get("mfa_methods", {}),
#                     "requires_phone": True,
#                     "billing_status": billing.get("status", "pending"),
#                     "requires_billing": billing.get("status") in ["pending", "blocked"]
#                 }

#             # 🔥 Generate OTP
#             otp = randint(100000, 999999)
#             expiry = datetime.utcnow() + timedelta(minutes=10)

#             await otp_collection.delete_many({
#                 "phone": phone,
#                 "type": "sms"
#             })

#             await otp_collection.insert_one({
#                 "phone": phone,
#                 "otp": otp,
#                 "type": "sms",
#                 "expires": expiry,
#                 "created_at": datetime.utcnow()
#             })

#             send_sms(
#                 to=phone,
#                 message=f"Your OTP is {otp}"
#             )

#             return {
#                 "message": "OTP sent to registered phone",
#                 "email": email,
#                 "role": "owner",
#                 "mfa_enabled": True,
#                 "primary_mfa": "sms",
#                 "mfa_methods": user.get("mfa_methods", {}),
#                 "phone": phone,
#                 "billing_status": billing.get("status", "pending"),
#                 "requires_billing": billing.get("status") in ["pending", "blocked"]
#             }

#         # ========================================================
#         # ✅ EMAIL / QR → KEEP EXISTING FLOW
#         # ========================================================
#         return {
#             "message": "Password verified",
#             "email": email,
#             "role": "owner",
#             "mfa_enabled": True,
#             "primary_mfa": primary_mfa,
#             "mfa_methods": user.get("mfa_methods", {}),
#             "billing_status": billing.get("status", "pending"),
#             "requires_billing": billing.get("status") in ["pending", "blocked"]
#         }

#     # ============================================================
#     # ✅ NO MFA → DIRECT LOGIN
#     # ============================================================
#     token = create_access_token(user)

#     return {
#         "message": "Login successful",
#         "access_token": token,
#         "email": email,
#         "role": "owner",
#         "mfa_enabled": False,
#         "billing_status": billing.get("status", "pending"),
#         "requires_billing": billing.get("status") in ["pending", "blocked"]
#     }
@router.post("/login")
async def owner_login(data: LoginRequest, request: Request, response: Response):
    identifier = (data.email or "").strip()
    if not identifier:
        raise HTTPException(
            status_code=400,
            detail="Enter your email or phone number",
        )

    from app.auth.captcha import verify_captcha_token

    if not verify_captcha_token(data.captcha_token, get_client_ip(request)):
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed")

    rate_key = identifier.lower() if looks_like_email(identifier) else identifier
    await enforce_auth_rate_limit(request, key=f"login:{rate_key}")

    user = await find_owner_by_login_identifier(
        identifier,
        users_collection=users_collection,
    )

    # Pending signup for this email (phone login won't match pending by phone alone below)
    pending_email = (
        identifier.lower()
        if looks_like_email(identifier)
        else (user.get("email") if user else None)
    )
    if pending_email:
        pending = await pending_signup_collection.find_one({
            "email": pending_email,
            "expires_at": {"$gt": datetime.utcnow()},
        })
        if pending:
            raise HTTPException(
                status_code=403,
                detail="Signup not completed yet. Please finish MFA verification first.",
            )

    # Also block login when identifier is a phone tied only to an unfinished signup
    if not user and not looks_like_email(identifier):
        try:
            phone_id = format_phone(identifier)
        except ValueError:
            phone_id = None
        if phone_id:
            pending_phone = await pending_signup_collection.find_one({
                "phone": phone_id,
                "expires_at": {"$gt": datetime.utcnow()},
            })
            if pending_phone:
                raise HTTPException(
                    status_code=403,
                    detail="Signup not completed yet. Please finish MFA verification first.",
                )

    stored_password = ""
    if user:
        stored_password = user.get("password") or user.get("password_hash") or ""

    if not user or not verify_password(data.password, stored_password):
        # Ops-only diagnostics (never returned to the client)
        if not user:
            print(f"login 401: no owner account for {identifier!r}")
        elif not stored_password:
            print(f"login 401: owner {user.get('email')} has empty password hash")
        else:
            print(
                f"login 401: bad password for {user.get('email')} "
                f"(hash_prefix={stored_password[:20]!r})"
            )
        raise HTTPException(
            status_code=401,
            detail="Invalid email, phone number, or password",
        )

    email = user["email"].lower().strip()
    await reset_auth_rate_limit(request, key=f"login:{rate_key}")

    billing = user.get("billing", {})

    methods = normalize_mfa_methods(user)
    if user.get("mfa_enabled") or any(methods.values()):
        if not any(methods.values()):
            await users_collection.update_one(
                {"_id": user["_id"]},
                {
                    "$set": {
                        "mfa_enabled": False,
                        "primary_mfa": None,
                        "mfa_methods": {
                            "email": False,
                            "authenticator": False,
                            "sms": False,
                        },
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
        else:
            response = mfa_login_response(user, billing)
            preferred = response.get("method")
            response["mfa_challenge_token"] = create_mfa_challenge_token(email)
            response["step_up_token"] = create_step_up_token(email)

            otp_sent, cooldown_seconds, otp_error = await _trigger_login_mfa_otp(
                request=request,
                user=user,
                method=preferred,
                email=email,
            )
            response["otp_sent"] = otp_sent
            if cooldown_seconds is not None:
                response["cooldown_seconds"] = cooldown_seconds
            if otp_error:
                response["otp_error"] = otp_error

            return response

    await record_owner_last_login(email)
    log_device_fingerprint(request, "login_success", subject=email)
    session = await issue_owner_session(response, user)
    session["email"] = email
    return session

# ============================================================
# 3️⃣ NEXT-OF-KIN LOGIN
# ============================================================
@router.post("/nextkin-login")
async def nextkin_login(request: Request, response: Response):
    data = await request.json()
    email = data.get("email", "").lower().strip()
    master_password = data.get("master_password")
    captcha_token = data.get("captcha_token")

    if not email or not master_password:
        raise HTTPException(status_code=400, detail="Email and master_password required")

    from app.auth.captcha import verify_captcha_token

    if not verify_captcha_token(captcha_token, get_client_ip(request)):
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed")

    await enforce_auth_rate_limit(request, key=f"nok-login:{email}")

    user = await users_collection.find_one({"email": email, "role": "nextkin"})
    stored_password = ""
    if user:
        stored_password = user.get("password_hash") or user.get("password") or ""

    if not user or not verify_password(master_password, stored_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    await reset_auth_rate_limit(request, key=f"nok-login:{email}")

    if user.get("access_revoked") or not user.get("immediate_access", False):
        raise HTTPException(status_code=403, detail=NOK_LOGIN_GENERIC)

    owner = await users_collection.find_one(
        {"_id": ObjectId(user["owner_id"]), "role": "owner"}
    )

    if owner and owner.get("billing", {}).get("status") == "blocked":
        raise HTTPException(status_code=403, detail=NOK_LOGIN_GENERIC)

    if owner:
        await notify_owner_nextkin_login(owner=owner, nextkin=user)

    await record_nextkin_last_login(str(user["_id"]))

    return await issue_nok_session(response, user)

# ============================================================
# 4️⃣ OWNER CREATES NEXT-OF-KIN ACCOUNT (FINAL VERSION)
# ============================================================
# === helper (place near top, after imports) ===
def generate_temp_password(length: int = 12) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


@router.post("/create-nextkin")
async def create_nextkin(
    payload: Union[NextKinCreateRequest, list[NextKinCreateRequest]],
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Create one or many Next-of-Kin users. Same endpoint handles single or list payloads."""

    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can create Next-of-Kin")

    owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
    if not owner:
        raise HTTPException(status_code=403, detail="Only owners can create Next-of-Kin")
    count = await users_collection.count_documents({
        "owner_id": str(owner["_id"]),
        "role": "nextkin",
    })

    enforce_usage(owner, "nextkin", count)
    # small inner util to avoid duplication
    async def _create_one(req: NextKinCreateRequest):
        from app.auth.nextkin_validation import prepare_nextkin_create_fields

        try:
            normalized = prepare_nextkin_create_fields(req)
        except ValueError as exc:
            return {
                "email": str(getattr(req, "email", "") or "").lower(),
                "status": "error",
                "error": str(exc),
            }

        email = normalized["email"]

        # 2️⃣ Prevent duplicate
        existing = await users_collection.find_one({"email": email})
        if existing:
            return {
                "email": email,
                "status": "error",
                "error": "Next-of-Kin already exists"
            }

        # 3️⃣ Ensure a temp password exists; store hash for auth, keep plain in master_password field (your current model)
        plain_password = req.master_password or generate_temp_password()

        new_nok = {
            "email": email,
            "full_name": normalized["full_name"],
            "relationship": normalized["relationship"],
            "phone_number": req.phone_number,

            "access_level": normalized["access_level"],
            "authorized_sections": normalized["authorized_sections"] or [],
            "immediate_access": False,
            "access_timing": "immediate" if req.immediate_access else "upon_death",
            "access_revoked": False,
            "nok_letter_received": (
                bool(req.nok_letter_received) if not req.immediate_access else False
            ),

            "password_card_generated": bool(
                req.password_card_generated or plain_password
            ),
            "master_password": plain_password,
            "card_storage_location": req.card_storage_location,
            "key_bag_location": req.key_bag_location,
            "documents_bag_location": req.documents_bag_location,
            "special_instructions": req.special_instructions,

            "password_hash": hash_password(plain_password),

            "role": "nextkin",
            "owner_id": str(owner["_id"]),
            "verified": True,
            "mfa_enabled": False,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        stored_nok = prepare_nextkin_profile_for_storage(new_nok)
        insert_res = await users_collection.insert_one(stored_nok)
        new_id = insert_res.inserted_id

        nextkin = load_nextkin_profile(
            await users_collection.find_one({"_id": new_id})
        )

        # ✅ CASE 1: Immediate access → approve + send ACCESS email (with password)
        if req.immediate_access:
            await users_collection.update_one(
                {"_id": new_id},
                {
                    "$set": {
                        "immediate_access": True,
                        "nok_letter_received": False,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )

            await send_nextkin_email(
                event=NextKinEmailEvent.ACCESS_APPROVED,
                nextkin=nextkin,
                owner=owner,
                plain_password=plain_password,
            )

        # CASE 2: No immediate access → invite email with temp password
        else:
            await send_nextkin_email(
                event=NextKinEmailEvent.CREATED,
                nextkin=nextkin,
                owner=owner,
                plain_password=plain_password,
            )

        # # 4️⃣ Email credentials (best-effort)
        # try:
        #     sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        #     owner_name = owner.get("full_name") or owner["email"]
        #     html = f"""
        #     <div style="font-family:Arial,sans-serif;line-height:1.6">
        #       <h3>Hello {req.full_name},</h3>
        #       <p>You’ve been added as a <strong>Next-of-Kin</strong> by <b>{owner_name}</b> in Orderly Affairs.</p>
        #       <p>Use these details to log in:</p>
        #       <ul>
        #         <li><b>Email:</b> {email}</li>
        #         <li><b>Temporary Password:</b> {plain_password}</li>
        #       </ul>
        #       <p>Please log in here: <a href="{settings.FRONTEND_URL}/nextkin-login">{settings.FRONTEND_URL}/nextkin-login</a></p>
        #       <p>After logging in, please change your password immediately.</p>
        #       <hr />
        #       <small>Created on {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</small>
        #     </div>
        #     """
        #     message = Mail(
        #         from_email=settings.EMAIL_SENDER,
        #         to_emails=email,
        #         subject="Orderly Affairs - Your Next-of-Kin Login Credentials",
        #         html_content=html,
        #     )
        #     sg.send(message)
        # except Exception as e:
        #     # don't fail the creation just because email failed
        #     print("⚠️ SendGrid Email Error:", e)

        return {
            "id": str(new_id),
            "email": email,
            "full_name": req.full_name,
            "relationship": req.relationship,
            "status": "ok",
            "message": f"Next-of-Kin '{req.full_name}' created successfully.",
            "master_password": plain_password,
        }

    # 5️⃣ Handle single or bulk payloads with SAME endpoint
    if isinstance(payload, list):
        results = []
        # Optional: dedupe emails inside the same request
        seen = set()
        for idx, item in enumerate(payload):
            if item.email.lower() in seen:
                results.append({
                    "index": idx,
                    "email": item.email.lower(),
                    "status": "error",
                    "error": "Duplicate email in request payload"
                })
                continue
            seen.add(item.email.lower())

            try:
                res = await _create_one(item)
                # include index to map back on the client
                res["index"] = idx
                results.append(res)
            except Exception as e:
                results.append({
                    "index": idx,
                    "email": item.email.lower(),
                    "status": "error",
                    "error": str(e),
                })
        return {"results": results}

    # single-object behavior (preserves your previous response shape)
    res = await _create_one(payload)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res["error"])

    return {
        "message": f"Next-of-Kin '{payload.full_name}' created successfully.",
        "email": res["email"],
        "relationship": payload.relationship,
        "owner": owner.get("full_name") or owner["email"],
        "id": res["id"],
        "temp_password_sent": True,
        "master_password": res.get("master_password") or "",
    }



# ============================================================
# 5️⃣ GET ALL NEXT-OF-KIN FOR LOGGED-IN OWNER
# ============================================================
@router.get("/my-nextkin")
async def get_my_nextkin(
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can view next-kin")
    owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    nextkins = users_collection.find({"owner_id": str(owner["_id"]), "role": "nextkin"})
    results = []
    async for nk in nextkins:
        nk = load_nextkin_profile(nk)
        results.append({
            "id": str(nk["_id"]),
            "email": nk["email"],
            "full_name": nk.get("full_name"),
            "relationship": nk.get("relationship"),
            "phone_number": nk.get("phone_number"),

            "access_level": nk.get("access_level"),
            "authorized_sections": nk.get("authorized_sections", []),
            "immediate_access": nk.get("immediate_access", False),
            "nok_letter_received": nk.get("nok_letter_received", False),

            "password_card_generated": nk.get("password_card_generated"),
            "has_master_password": bool(
                nk.get("password_hash") or nk.get("master_password")
            ),
            # Owner-only: return plaintext so Access Management cards / print
            # card can show the existing login password with the eye toggle.
            "master_password": nk.get("master_password") or "",
            "card_storage_location": nk.get("card_storage_location"),
            "key_bag_location": nk.get("key_bag_location"),
            "documents_bag_location": nk.get("documents_bag_location"),
            "special_instructions": nk.get("special_instructions"),

            "created_at": nk.get("created_at"),
            "updated_at": nk.get("updated_at"),
        })

    return results
# ============================================================
# 13️⃣ UPDATE NEXT-OF-KIN (Owner only)
# ============================================================
@router.put("/update-nextkin/{nextkin_id}")
async def update_nextkin(
    nextkin_id: str,
    payload: NextKinUpdateRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can update Next-of-Kin")

    owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    nextkin = await users_collection.find_one(
        {"_id": ObjectId(nextkin_id), "role": "nextkin", "owner_id": str(owner["_id"])}
    )
    if not nextkin:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found or not linked to this owner")

    current_profile = load_nextkin_profile(dict(nextkin)) or dict(nextkin)
    previous_password = current_profile.get("master_password")

    # ✅ Only update provided fields
    update_data = {k: v for k, v in payload.dict().items() if v is not None}

    if update_data.get("immediate_access") is True:
        update_data["nok_letter_received"] = False

    password_changed = False
    new_password = (payload.master_password or "").strip() or None
    if new_password and new_password != (previous_password or ""):
        password_changed = True
        update_data["password_hash"] = hash_password(new_password)
        update_data["master_password"] = new_password
    elif "master_password" in update_data and not new_password:
        update_data.pop("master_password", None)

    if not update_data:
        raise HTTPException(status_code=400, detail="No valid fields provided to update")

    merged_profile = dict(current_profile)
    merged_profile.update(update_data)
    merged_profile["owner_id"] = str(owner["_id"])
    merged_profile["_id"] = nextkin["_id"]
    stored_profile = prepare_nextkin_profile_for_storage(merged_profile)
    stored_profile.pop("_id", None)

    unset = {
        key: ""
        for key in (
            "card_storage_location",
            "key_bag_location",
            "documents_bag_location",
            "special_instructions",
        )
        if key in nextkin
    }

    update_doc: dict = {"$set": stored_profile}
    if unset:
        update_doc["$unset"] = unset

    await users_collection.update_one({"_id": ObjectId(nextkin_id)}, update_doc)

    password_email_sent = False
    if password_changed and new_password:
        updated_nextkin = load_nextkin_profile(
            await users_collection.find_one({"_id": ObjectId(nextkin_id)})
        )
        if updated_nextkin:
            await send_nextkin_email(
                event=NextKinEmailEvent.PASSWORD_UPDATED,
                nextkin=updated_nextkin,
                owner=owner,
                plain_password=new_password,
            )
            password_email_sent = True

    return {
        "message": f"Next-of-Kin updated successfully.",
        "nextkin_id": nextkin_id,
        "updated_fields": list(update_data.keys()),
        "password_email_sent": password_email_sent,
    }

# ============================================================
# 14️⃣ DELETE NEXT-OF-KIN (Owner only)
# ============================================================
@router.delete("/delete-nextkin/{nextkin_id}")
async def delete_nextkin(
    nextkin_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """
    Allows an owner to delete a Next-of-Kin they created.
    """
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can delete Next-of-Kin")

    # 2️⃣ Find owner
    owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    # 3️⃣ Find and delete nextkin
    nextkin = await users_collection.find_one(
        {"_id": ObjectId(nextkin_id), "role": "nextkin", "owner_id": str(owner["_id"])}
    )
    if not nextkin:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found or not linked to this owner")

    await users_collection.delete_one({"_id": ObjectId(nextkin_id)})

    # 4️⃣ (Optional) Send notification email
    try:
        from app.notifications.email_layout import email_callout, escape, render_simple_email

        owner_name = await resolve_owner_display_name(owner)
        nk_name = resolve_nextkin_display_name(nextkin)
        sg = sendgrid.SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        message = Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=nextkin["email"],
            subject="Orderly Affairs - Next-of-Kin Account Deleted",
            html_content=render_simple_email(
                title="Next-of-Kin account deleted",
                greeting_name=nk_name,
                paragraphs=[
                    f"Your Next-of-Kin account under <b>{escape(owner_name)}</b> "
                    "has been deleted.",
                    f"If you believe this was a mistake, please contact "
                    f"{escape(owner_name)} directly.",
                ],
                callout_html=email_callout(
                    f"Deleted on {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    tone="info",
                ),
                preheader="Your Next-of-Kin account was deleted",
            ),
        )
        sg.send(message)
    except Exception as e:
        print("⚠️ SendGrid delete notification failed:", e)

    return {
        "message": f"Next-of-Kin '{nextkin.get('full_name') or nextkin['email']}' deleted successfully.",
        "deleted_id": nextkin_id,
    }
# app/auth/routes.py  (ADD THIS NEW ENDPOINT ANYWHERE AFTER THE ROUTER IS CREATED)


@router.get("/nextkin-access")
async def get_nextkin_access(
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(
        request,
        authorization,
        access_cookie=NOK_ACCESS_COOKIE,
    )
    if decoded.get("role") != "nextkin":
        raise HTTPException(status_code=403, detail="Only next-of-kin can access")

    sub = decoded.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    try:
        nextkin_id = ObjectId(sub)
    except InvalidId:
        raise HTTPException(status_code=401, detail="Invalid token – please log in again")

    nextkin = await users_collection.find_one(
        {"_id": nextkin_id, "role": "nextkin"}
    )

    if not nextkin:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found")

    if not nextkin.get("immediate_access", False):
        raise HTTPException(status_code=403, detail="Access not approved")

    access_level = nextkin.get("access_level", "Full Kit Access")
    full_access = access_level == "Full Kit Access"

    owner = None
    try:
        owner = await users_collection.find_one(
            {"_id": ObjectId(nextkin["owner_id"]), "role": "owner"}
        )
    except Exception:
        owner = None

    owner_summary = None
    if owner:
        owner_summary = {
            "id": str(owner["_id"]),
            "email": owner.get("email"),
            "full_name": owner.get("full_name"),
            "status": owner.get("owner_status", "alive"),
        }

    return {
        "full_access": full_access,
        "authorized_sections": "all" if full_access else nextkin.get("authorized_sections", []),
        "access_level": access_level,
        "immediate_access": True,
        "access_timing": nextkin.get("access_timing"),
        "nok_letter_received": nextkin.get("nok_letter_received", False),
        "owner_id": nextkin["owner_id"],
        "owner": owner_summary,
        "nextkin": {
            "id": str(nextkin["_id"]),
            "email": nextkin["email"],
            "full_name": nextkin.get("full_name"),
            "relationship": nextkin.get("relationship"),
        },
        "created_at": nextkin.get("created_at"),
        "updated_at": nextkin.get("updated_at"),
    }
# ============================================================
#  Next-of-Kin's access approved all
# ============================================================
@router.post("/approve-nextkin-access/{nextkin_id}")
async def approve_nextkin_access(
    nextkin_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can approve access")

    owner = await users_collection.find_one(
        {"email": decoded["sub"], "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    nextkin = await users_collection.find_one(
        {
            "_id": ObjectId(nextkin_id),
            "role": "nextkin",
            "owner_id": str(owner["_id"]),
        }
    )
    if not nextkin:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found")

    nextkin_profile = load_nextkin_profile(dict(nextkin)) or dict(nextkin)
    await _approve_and_notify_if_needed(
        nextkin_profile,
        owner,
        approved=True,
        plain_password=nextkin_profile.get("master_password"),
    )

    return {
        "message": "Next-of-Kin access approved",
        "nextkin_email": nextkin["email"],
        "immediate_access": True,
    }


@router.post("/approve-all-nextkin-access")
async def approve_all_nextkin_access(
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can approve access")

    owner = await users_collection.find_one(
        {"email": decoded["sub"], "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    cursor = users_collection.find(
        {
            "role": "nextkin",
            "owner_id": str(owner["_id"]),
            "immediate_access": False,
            "access_revoked": {"$ne": True},
        }
    )
    approved = 0
    async for nextkin in cursor:
        nextkin_profile = load_nextkin_profile(dict(nextkin)) or dict(nextkin)
        await _approve_and_notify_if_needed(
            nextkin_profile,
            owner,
            approved=True,
            plain_password=nextkin_profile.get("master_password"),
        )
        approved += 1

    return {
        "message": f"Approved access for {approved} Next-of-Kin",
        "approved_count": approved,
    }

# ============================================================
# REVOKE a single Next-of-Kin's access
# ============================================================
@router.post("/revoke-nextkin-access/{nextkin_id}")
async def revoke_nextkin_access(
    nextkin_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can manage Next-of-Kin access")

    # owner
    owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    # target nextkin
    nextkin = await users_collection.find_one(
        {"_id": ObjectId(nextkin_id), "role": "nextkin", "owner_id": str(owner["_id"])}
    )
    if not nextkin:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found or not linked to this owner")

    await _approve_and_notify_if_needed(nextkin, owner, approved=False)

    return {
        "message": "Next-of-Kin access has been revoked",
        "nextkin_email": nextkin["email"],
        "immediate_access": False,
    }

# ============================================================
# REVOKE access for ALL Next-of-Kin under the authenticated owner
# ============================================================
@router.post("/revoke-all-nextkin-access")
async def revoke_all_nextkin_access(
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can manage Next-of-Kin access")

    owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    cursor = users_collection.find({"role": "nextkin", "owner_id": str(owner["_id"])})
    nextkins = [nk async for nk in cursor]

    if not nextkins:
        return {"message": "No Next-of-Kin found for this owner", "updated": 0, "emailed": 0}

    # bulk update in DB first
    now = datetime.utcnow()
    bulk_res = await users_collection.update_many(
        {"role": "nextkin", "owner_id": str(owner["_id"])},
        {"$set": {"immediate_access": False, "updated_at": now}},
    )

    # best-effort notify each (don’t block if any fails)
    emailed = 0
    for nk in nextkins:
        try:
            await _approve_and_notify_if_needed(nk, owner, approved=False)
            emailed += 1
        except Exception:
            pass

    return {
        "message": "All Next-of-Kin access revoked for this owner",
        "updated": getattr(bulk_res, "modified_count", 0),
        "emailed": emailed,
        "owner_id": str(owner["_id"]),
    }

# ============================================================
# 6️⃣ VERIFY TOTP
# ============================================================
@router.post("/verify-totp")
async def verify_totp(
    payload: VerifyTOTPRequest,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
):
    email = payload.email.lower().strip()
    pending = await get_active_pending_signup(email)

    await require_login_mfa_proof(
        email=email,
        mfa_challenge_token=payload.mfa_challenge_token,
        authorization=authorization,
        request=request,
        pending=pending,
    )

    user = await users_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)
    totp_secret = read_user_totp_secret(user)
    if not totp_secret:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)
    totp = pyotp.TOTP(totp_secret)
    if not totp.verify(payload.code):
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    methods = normalize_mfa_methods(user)
    if not methods["authenticator"]:
        raise HTTPException(status_code=403, detail="Authenticator MFA not enabled")

    await users_collection.update_one(
        {"email": payload.email.lower()},
        {
            "$set": {
                "verified": True,
                "mfa_enabled": True,
                "primary_mfa": user.get("primary_mfa") or "authenticator",
                "mfa_methods.authenticator": True,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    updated_user = await users_collection.find_one({"email": email})
    if updated_user.get("role") == "owner":
        await record_owner_last_login(updated_user["email"])
    return await issue_owner_session(response, updated_user)


# ============================================================
# 7️⃣ GENERATE MFA QR
# ============================================================
@router.post("/generate-mfa")
async def generate_mfa(
    payload: EmailRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    email = payload.email.lower().strip()
    user = await users_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)
    methods = normalize_mfa_methods(user)
    if user.get("mfa_linked") and methods["authenticator"]:
        raise HTTPException(status_code=400, detail="Authenticator already linked")

    authorized_owner = await get_authorized_owner_for_email(
        email,
        authorization,
        request=request,
    )
    if not authorized_owner:
        raise HTTPException(
            status_code=403,
            detail="Sign in and enable authenticator MFA from Vault Settings."
        )

    secret = pyotp.random_base32()
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name="Orderly Affairs")
    qr = qrcode.make(uri)
    buf = BytesIO()
    qr.save(buf, format="PNG")
    img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    await users_collection.update_one(
        {"email": email},
        {"$set": {"provisioned_secret": encrypt_totp_value(email, secret, pending=True)}},
    )
    return {"qrCodeUrl": f"data:image/png;base64,{img_base64}"}


# ============================================================
# 8️⃣ LINK AUTHENTICATOR
# ============================================================
@router.post("/link-authenticator")
async def link_authenticator(
    payload: LinkAuthenticatorRequest,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
):
    email = payload.email.lower().strip()

    # first check pending signup
    pending = await pending_signup_collection.find_one({
        "email": email,
        "mfa_method": "authenticator",
        "expires_at": {"$gt": datetime.utcnow()}
    })

    if pending:
        secret = read_pending_totp_secret(pending)
        if not secret:
            raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

        totp = pyotp.TOTP(secret)
        if not totp.verify(payload.code):
            raise HTTPException(status_code=400, detail="Invalid verification code")

        pending["totp_secret"] = encrypt_totp_value(email, secret)
        created_user = await create_real_user_from_pending(pending)
        return await issue_owner_session(response, created_user)

    # existing real user flow
    user = await users_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

    authorized_owner = await get_authorized_owner_for_email(
        email,
        authorization,
        request=request,
    )
    if not authorized_owner:
        raise HTTPException(
            status_code=403,
            detail="Sign in and enable authenticator MFA from Vault Settings."
        )

    secret = read_user_provisioned_secret(user)
    if not secret:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

    totp = pyotp.TOTP(secret)
    if not totp.verify(payload.code):
        raise HTTPException(status_code=400, detail="Invalid verification code")

    await users_collection.update_one(
        {"email": email},
        {
            "$set": {
                "totp_secret": encrypt_totp_value(email, secret),
                "provisioned_secret": None,
                "mfa_linked": True,
                "mfa_enabled": True,
                "verified": True,
                "primary_mfa": user.get("primary_mfa") or "authenticator",
                "mfa_methods.authenticator": True,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    updated_user = await users_collection.find_one({"email": email})
    return {
        "authenticated": True,
        "message": "Authenticator linked successfully",
        "mfa_enabled": True,
        "primary_mfa": updated_user.get("primary_mfa"),
        "mfa_methods": normalize_mfa_methods(updated_user),
    }
# ============================================================
# 9️⃣ EMAIL OTP — SEND & VERIFY
# ============================================================
@router.post("/send-email")
async def send_email_otp(
    payload: EmailRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    email = payload.email.lower().strip()
    is_signup_flow = (payload.flow or "").lower().strip() == "signup"

    pending = await pending_signup_collection.find_one({
        "email": email,
        "mfa_method": "email",
        "expires_at": {"$gt": datetime.utcnow()}
    })

    if is_signup_flow and not pending:
        raise HTTPException(
            status_code=400,
            detail="Signup session expired. Please start signup again.",
        )

    if not pending:
        user = await users_collection.find_one({"email": email, "role": "owner"})
        if not user:
            raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

        methods = normalize_mfa_methods(user)
        authorized_owner = await get_authorized_owner_for_email(
            email, authorization, request=request
        )
        if not methods["email"] and not authorized_owner:
            raise HTTPException(
                status_code=403,
                detail="Email MFA is not linked. Sign in and enable it from Vault Settings."
            )

        if not authorized_owner:
            await require_login_mfa_proof(
                email=email,
                mfa_challenge_token=payload.mfa_challenge_token,
                authorization=authorization,
                request=request,
                pending=None,
            )

    pending_id = pending["_id"] if pending else None

    async def _store_send_email_otp(
        target_email: str, otp: int, expiry: datetime
    ) -> None:
        if pending_id is not None:
            await pending_signup_collection.update_one(
                {"_id": pending_id},
                {
                    "$set": {
                        "email_otp_hash": hash_otp_value(
                            target_email, otp, "signup"
                        ),
                        "email_otp_expires": expiry,
                        "updated_at": datetime.utcnow(),
                    },
                    "$unset": {"email_otp": ""},
                },
            )
        else:
            await _store_login_email_otp(target_email, otp, expiry)

    async def _rollback_send_email_otp(target_email: str) -> None:
        if pending_id is not None:
            await pending_signup_collection.update_one(
                {"_id": pending_id},
                {
                    "$set": {
                        "email_otp_hash": None,
                        "email_otp_expires": None,
                        "updated_at": datetime.utcnow(),
                    },
                    "$unset": {"email_otp": ""},
                },
            )
        else:
            await _rollback_login_email_otp(target_email)

    try:
        email_result = await send_email_otp_secure(
            request=request,
            email=email,
            captcha_token=payload.captcha_token,
            session_id=payload.otp_session_id,
            # Signup never requires Cloudflare (pending signup or explicit flow).
            skip_captcha=(
                pending is not None
                or is_signup_flow
                or verify_mfa_challenge_token(
                    payload.mfa_challenge_token, email
                )
            ),
            store_otp=_store_send_email_otp,
            rollback_otp=_rollback_send_email_otp,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to send verification email: {str(e)}",
        )

    return {
        "message": (
            f"Verification code already sent to {email}"
            if email_result.get("already_sent")
            else f"Verification code sent to {email}"
        ),
        "cooldown_seconds": email_result["cooldown_seconds"],
        "already_sent": bool(email_result.get("already_sent")),
    }


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is not None:
        return value.replace(tzinfo=None)
    return value


def _pending_email_otp_matches(email: str, otp_hash: str, code: int) -> bool:
    """Accept current signup hashes and older type scopes if present."""
    for otp_type in ("signup", "login_email", ""):
        if verify_stored_otp(
            {"email": email, "otp_hash": otp_hash, "type": otp_type},
            code,
        ):
            return True
    # Also try string form (some clients historically posted digit strings)
    for otp_type in ("signup", "login_email", ""):
        if verify_stored_otp(
            {"email": email, "otp_hash": otp_hash, "type": otp_type},
            str(code),
        ):
            return True
    return False


@router.post("/verify-email")
async def verify_email_code(
    payload: VerifyEmailRequest,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
):
    email = payload.email.lower().strip()
    now = datetime.utcnow()

    await ensure_email_verify_not_locked(email)

    # Pending signup — load by email first (avoid tz mismatches on Mongo $gt)
    pending = await pending_signup_collection.find_one({
        "email": email,
        "mfa_method": "email",
    })
    if pending:
        pending_expires = _naive_utc(pending.get("expires_at"))
        if pending_expires is not None and now > pending_expires:
            pending = None

    if pending:
        otp_hash = pending.get("email_otp_hash")
        otp_expires = _naive_utc(pending.get("email_otp_expires"))
        legacy_otp = pending.get("email_otp")

        if (not otp_hash and legacy_otp is None) or not otp_expires:
            print(f"verify-email 400: no signup OTP for {email}")
            raise HTTPException(
                status_code=400,
                detail="No verification code found. Request a new code.",
            )

        if now > otp_expires:
            print(f"verify-email 400: signup OTP expired for {email}")
            raise HTTPException(
                status_code=400,
                detail="Code expired. Request a new code.",
            )

        otp_ok = (
            _pending_email_otp_matches(email, otp_hash, payload.code)
            if otp_hash
            else str(legacy_otp) == str(payload.code)
        )
        if not otp_ok:
            print(f"verify-email 400: bad signup OTP for {email}")
            await record_email_verify_attempt(
                request=request,
                email=email,
                success=False,
                session_id=payload.otp_session_id,
            )

        await record_email_verify_attempt(
            request=request,
            email=email,
            success=True,
            session_id=payload.otp_session_id,
        )

        created_user = await create_real_user_from_pending(pending)
        return await issue_owner_session(response, created_user)

    await require_login_mfa_proof(
        email=email,
        mfa_challenge_token=payload.mfa_challenge_token,
        authorization=authorization,
        request=request,
        pending=None,
    )

    # otherwise normal login email MFA — prefer newest OTP
    record = await otp_collection.find_one(
        {"email": email},
        sort=[("created_at", -1), ("expires", -1)],
    )
    if not record:
        print(f"verify-email 400: no login OTP for {email}")
        raise HTTPException(
            status_code=400,
            detail="No verification code found. Request a new code.",
        )

    record_expires = _naive_utc(record.get("expires"))
    if record_expires is None or now > record_expires:
        print(f"verify-email 400: login OTP expired for {email}")
        raise HTTPException(
            status_code=400,
            detail="Code expired. Request a new code.",
        )

    if not verify_stored_otp(record, payload.code):
        print(f"verify-email 400: bad login OTP for {email}")
        await record_email_verify_attempt(
            request=request,
            email=email,
            success=False,
            session_id=payload.otp_session_id,
        )

    user = await users_collection.find_one({
        "email": email,
        "role": "owner"
    })

    if not user:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

    methods = normalize_mfa_methods(user)
    authorized_owner = await get_authorized_owner_for_email(
        email,
        authorization,
        request=request,
    )
    if not methods["email"] and not authorized_owner:
        raise HTTPException(
            status_code=403,
            detail="Email MFA is not linked. Sign in and enable it from Vault Settings."
        )

    await record_email_verify_attempt(
        request=request,
        email=email,
        success=True,
        session_id=payload.otp_session_id,
    )

    await otp_collection.delete_many({"email": email})

    await users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "verified": True,
                "mfa_enabled": True,
                "primary_mfa": user.get("primary_mfa") or "email",
                "mfa_methods.email": True,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    updated_user = await users_collection.find_one({"_id": user["_id"]})
    if updated_user.get("role") == "owner":
        await record_owner_last_login(updated_user["email"])
    return await issue_owner_session(response, updated_user)
# ============================================================
# 🔟 REFRESH TOKEN
# ============================================================
@router.post("/refresh-token")
async def refresh_token(request: Request, response: Response):
    owner_refresh = request.cookies.get("oa_refresh_token")
    nok_refresh = request.cookies.get("oa_nok_refresh_token")

    if owner_refresh:
        try:
            return await refresh_session_from_cookie(
                response,
                request,
                role="owner",
            )
        except ValueError:
            pass

    if nok_refresh:
        try:
            return await refresh_session_from_cookie(
                response,
                request,
                role="nextkin",
            )
        except ValueError:
            pass

    raise HTTPException(status_code=401, detail="Missing refresh session")


@router.get("/session")
async def get_session(request: Request):
    """Return auth state without exposing JWT values to JavaScript."""

    for cookie_name, role in (
        (OWNER_ACCESS_COOKIE, "owner"),
        (NOK_ACCESS_COOKIE, "nextkin"),
    ):
        token = request.cookies.get(cookie_name)
        if not token:
            continue
        decoded = verify_token(token)
        if not decoded:
            continue

        if role == "nextkin":
            user = await users_collection.find_one(
                {"_id": ObjectId(decoded["sub"]), "role": "nextkin"}
            )
        else:
            user = await users_collection.find_one(
                {"email": decoded["sub"], "role": "owner"}
            )

        if not user:
            continue

        payload = {
            "authenticated": True,
            "role": role,
            "email": user.get("email"),
            "owner_id": str(user.get("owner_id") or user.get("_id")),
        }
        if role == "owner":
            from app.billing.access import billing_session_flags

            billing = user.get("billing", {})
            flags = billing_session_flags(billing)
            payload["billing_status"] = flags["billing_status"]
            payload["requires_billing"] = flags["requires_billing"]
            payload["billing_only"] = flags["billing_only"]
            payload["is_complimentary"] = flags["is_complimentary"]
            payload["auto_renew"] = flags["auto_renew"]
            payload["trial_mode"] = flags["trial_mode"]
            payload["lock_message"] = flags["lock_message"]
        return payload

    return {"authenticated": False}


# ============================================================
# 11️⃣ /me (Protected)
# ============================================================
@router.get("/me")
async def get_me(request: Request, authorization: str | None = Header(None)):
    token = extract_access_token(
        request,
        authorization,
        access_cookie=OWNER_ACCESS_COOKIE,
        required=False,
    )
    if not token:
        token = extract_access_token(
            request,
            authorization,
            access_cookie=NOK_ACCESS_COOKIE,
            required=False,
        )
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")

    decoded = verify_token(token)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    role = decoded.get("role", "owner")
    if role == "nextkin":
        try:
            user = await users_collection.find_one(
                {"_id": ObjectId(decoded["sub"]), "role": "nextkin"}
            )
        except InvalidId:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    else:
        user = await users_collection.find_one(
            {"email": decoded["sub"], "role": "owner"}
        )

    if not user:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)
    
    enforce_billing(user)

    return {
        "email": user["email"],
        "phone": user.get("phone"),
        "role": user.get("role", "owner"),
        "mfa_enabled": any(normalize_mfa_methods(user).values()),
        "primary_mfa": user.get("primary_mfa"),
        "mfa_methods": normalize_mfa_methods(user),
    }

@router.post("/mfa/disable")
async def disable_mfa_method(
    payload: MFAMethodRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    method = payload.method
    if method not in MFA_METHODS:
        raise HTTPException(status_code=400, detail="Invalid MFA method")

    decoded = decode_access_token(request, authorization)

    user = await users_collection.find_one({
        "email": decoded["sub"],
        "role": "owner"
    })
    if not user:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

    require_step_up_auth(
        user=user,
        password=payload.password,
        mfa_challenge_token=payload.mfa_challenge_token,
        step_up_token=payload.step_up_token,
    )

    methods = normalize_mfa_methods(user)
    if methods.get(method) and sum(1 for enabled in methods.values() if enabled) <= 1:
        raise HTTPException(
            status_code=400,
            detail="At least one MFA method must remain linked."
        )

    methods[method] = False
    next_primary = user.get("primary_mfa")
    if next_primary == method:
        next_primary = first_enabled_mfa_method(methods)

    update_doc = {
        "$set": {
            f"mfa_methods.{method}": False,
            "mfa_enabled": any(methods.values()),
            "primary_mfa": next_primary,
            "updated_at": datetime.utcnow(),
        }
    }

    if method == "authenticator":
        update_doc["$set"]["mfa_linked"] = False
        update_doc["$unset"] = {
            "totp_secret": "",
            "provisioned_secret": "",
        }

    await users_collection.update_one({"_id": user["_id"]}, update_doc)

    return {
        "message": f"{method} MFA disabled",
        "mfa_enabled": any(methods.values()),
        "primary_mfa": next_primary,
        "mfa_methods": methods,
    }

@router.post("/mfa/reset")
async def reset_mfa(
    payload: MFAResetRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)

    user = await users_collection.find_one({"email": decoded["sub"]})
    if not user:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

    require_step_up_auth(
        user=user,
        password=payload.password,
        mfa_challenge_token=payload.mfa_challenge_token,
        step_up_token=payload.step_up_token,
    )

    log_device_fingerprint(request, "mfa_reset", subject=decoded["sub"])

    await users_collection.update_one(
        {"email": user["email"]},
        {
            "$set": {
                "mfa_enabled": False,
                "primary_mfa": None,
                "mfa_methods": {
                    "email": False,
                    "authenticator": False,
                    "sms": False,
                },
            },
            "$unset": {
                "totp_secret": "",
                "provisioned_secret": "",
            },
        },
    )


    return {"message": "MFA reset. Please set up a new method."}


@router.post("/owner-logout")
async def owner_logout(request: Request, response: Response):
    return await logout_owner_session(response, request)


@router.post("/delete-account")
async def delete_owner_account(
    payload: DeleteAccountRequest,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
):
    """
    Permanently delete the owner account and all owned data:
    Cloudinary folder (docs/images), message audio/video, letters media,
    local AI uploads, sections, NOKs, kits, billing customer.
    """
    from app.auth.account_purge_service import (
        DELETE_CONFIRM_PHRASE,
        purge_owner_account,
    )

    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can delete their account")

    owner = await users_collection.find_one(
        {"email": decoded["sub"], "role": "owner"},
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    confirm = (payload.confirm or "").strip().upper()
    if confirm != DELETE_CONFIRM_PHRASE:
        raise HTTPException(
            status_code=400,
            detail=f'Type {DELETE_CONFIRM_PHRASE} to confirm account deletion',
        )

    require_step_up_auth(
        user=owner,
        password=payload.password,
        mfa_challenge_token=payload.mfa_challenge_token,
        step_up_token=payload.step_up_token,
    )

    summary = await purge_owner_account(owner)

    # End session after wipe (user row already gone — only clear cookies).
    from app.security.cookie_auth import clear_auth_cookies

    clear_auth_cookies(response, owner=True, nok=True)

    return {
        "success": True,
        "message": "Account and all stored media deleted permanently.",
        "summary": summary,
    }


@router.post("/nextkin-logout")
async def nextkin_logout(request: Request, response: Response):
    return await logout_nok_session(response, request)

@router.post("/nextkin/report-owner-deceased")
async def nextkin_report_owner_deceased(
    payload: ReportOwnerDeceasedRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="You must confirm this report to continue",
        )

    decoded = decode_access_token(
        request,
        authorization,
        access_cookie=NOK_ACCESS_COOKIE,
    )
    if decoded.get("role") != "nextkin":
        raise HTTPException(
            status_code=403,
            detail=NOK_LOGIN_GENERIC,
        )

    try:
        nextkin_id = ObjectId(decoded["sub"])
    except (InvalidId, KeyError, TypeError):
        raise HTTPException(status_code=401, detail=NOK_LOGIN_GENERIC)

    nextkin = await users_collection.find_one(
        {"_id": nextkin_id, "role": "nextkin"}
    )
    if not nextkin:
        raise HTTPException(status_code=400, detail=NOK_LOGIN_GENERIC)

    if not verify_password(payload.master_password, nextkin.get("password_hash", "")):
        raise HTTPException(status_code=401, detail=NOK_LOGIN_GENERIC)

    if not nextkin.get("immediate_access", False) or nextkin.get("access_revoked"):
        raise HTTPException(status_code=403, detail=NOK_LOGIN_GENERIC)

    try:
        owner = await users_collection.find_one(
            {"_id": ObjectId(nextkin["owner_id"]), "role": "owner"}
        )
    except Exception:
        owner = None

    if not owner:
        raise HTTPException(status_code=400, detail=NOK_LOGIN_GENERIC)

    result = await mark_owner_deceased(
        owner_id=str(owner["_id"]),
        reported_by_nextkin_id=str(nextkin["_id"]),
        source="manual_report",
    )

    if result.get("already_deceased"):
        return {
            "status": "deceased",
            "already_reported": True,
            "message": "This passing has already been recorded.",
            "upon_death_granted": 0,
        }

    return {
        "status": "deceased",
        "already_reported": False,
        "message": (
            "Passing recorded. Death-triggered letters and upon-death access "
            "notifications have been sent."
        ),
        "upon_death_granted": result.get("upon_death_granted", 0),
    }


@router.put("/owner/status")
async def update_owner_status(
    status: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)

    if status not in ["alive", "deceased"]:
        raise HTTPException(400, "Invalid status")

    # await users_collection.update_one(
    #     {"_id": user["sub"]},
    #     {"$set": {"owner_status": status}}
    # )
    await users_collection.update_one(
    {"email": decoded["sub"], "role": "owner"},
    {"$set": {"owner_status": status}}
)

    # 🔥 TRIGGER DEATH LETTERS
    if status == "deceased":
        await trigger_death_letters(decoded["sub"])

    return {"status": "updated"}

# ============================================================
# OWNER REQUEST PASSWORD RESET
# ============================================================
PASSWORD_RESET_GENERIC_MESSAGE = (
    "If an account exists for that email, a password reset code has been sent."
)


@router.post("/request-password-reset")
async def owner_request_password_reset(payload: OwnerResetRequest, request: Request):
    from app.auth.captcha import verify_captcha_token

    if not verify_captcha_token(payload.captcha_token, get_client_ip(request)):
        raise HTTPException(status_code=400, detail="CAPTCHA verification failed")

    email = payload.email.lower()

    owner = await users_collection.find_one({
        "email": email,
        "role": "owner"
    })

    if not owner:
        return {"message": PASSWORD_RESET_GENERIC_MESSAGE}

    window_minutes = settings.AUTH_RATE_LIMIT_WINDOW_MINUTES
    window_start = datetime.utcnow() - timedelta(minutes=window_minutes)

    attempts = await otp_collection.count_documents({
        "email": email,
        "type": "password_reset",
        "created_at": {"$gte": window_start},
    })

    if attempts >= 5:
        oldest = await otp_collection.find_one(
            {
                "email": email,
                "type": "password_reset",
                "created_at": {"$gte": window_start},
            },
            sort=[("created_at", 1)],
        )
        retry_after = window_minutes * 60
        if oldest and oldest.get("created_at"):
            created = oldest["created_at"]
            if getattr(created, "tzinfo", None) is not None:
                created = created.replace(tzinfo=None)
            expires_at = created + timedelta(minutes=window_minutes)
            retry_after = max(
                int((expires_at - datetime.utcnow()).total_seconds()),
                1,
            )
        # Never longer than 15–30 minutes
        retry_after = min(
            retry_after,
            settings.AUTH_RATE_LIMIT_MAX_LOCK_SECONDS,
            settings.OTP_BURST_WINDOW_MINUTES * 60,
        )
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many password reset requests. "
                f"Try again in {retry_after} seconds."
            ),
            headers={"Retry-After": str(retry_after)},
        )

    otp = randint(100000, 999999)
    expiry = datetime.utcnow() + timedelta(minutes=10)

    # Only one active reset OTP per email (avoid find_one matching a stale code)
    await otp_collection.delete_many({
        "email": email,
        "type": "password_reset",
    })

    reset_doc = otp_storage_fields(email, otp, "password_reset")
    reset_doc["expires"] = expiry
    reset_doc["created_at"] = datetime.utcnow()
    await otp_collection.insert_one(reset_doc)

    # Persist first, then send. On send failure: remove OTP and error out —
    # never return success after failing to deliver the code.
    try:
        from app.notifications.email_layout import (
            email_callout,
            email_code_box,
            p,
            render_email,
        )

        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        message = Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=email,
            subject="Orderly Affairs Password Reset",
            html_content=render_email(
                title="Password reset",
                preheader=f"Your password reset code is {otp}",
                body_html="".join(
                    [
                        p("Hello,"),
                        p(
                            "Use the code below to reset your password. It expires "
                            "in <b>10 minutes</b>."
                        ),
                        email_code_box(otp),
                        email_callout(
                            "If you did not request a password reset, you can "
                            "safely ignore this email.",
                            tone="info",
                        ),
                    ]
                ),
            ),
        )
        sg.send(message)
    except Exception as e:
        print("SendGrid error:", e)
        await otp_collection.delete_many({
            "email": email,
            "type": "password_reset",
        })
        raise HTTPException(
            status_code=400,
            detail="Failed to send password reset code. Please try again.",
        ) from e

    return {"message": PASSWORD_RESET_GENERIC_MESSAGE}

# ============================================================
# OWNER RESET PASSWORD
# ============================================================
@router.post("/reset-password")
async def owner_reset_password(payload: OwnerResetPassword, request: Request):
    # Captcha is required on /request-password-reset only. Turnstile tokens are
    # single-use, so requiring captcha again here caused systematic 400s after a
    # successful code send. The emailed OTP is the second factor for this step.
    email = payload.email.lower().strip()

    await enforce_auth_rate_limit(request, key=f"reset-password:{email}")
    await ensure_otp_verify_not_locked("password_reset", email)

    owner = await users_collection.find_one({
        "email": email,
        "role": "owner"
    })

    if not owner:
        await record_otp_verify_attempt(
            request=request,
            scope="password_reset",
            email=email,
            success=False,
            generic_error=PASSWORD_RESET_GENERIC_ERROR,
        )

    record = await otp_collection.find_one(
        {"email": email, "type": "password_reset"},
        sort=[("created_at", -1)],
    )

    if not record or not verify_stored_otp(record, payload.otp):
        print(f"reset-password 400: invalid otp for {email}")
        await record_otp_verify_attempt(
            request=request,
            scope="password_reset",
            email=email,
            success=False,
            generic_error=PASSWORD_RESET_GENERIC_ERROR,
        )

    expires = record.get("expires")
    if expires is not None and getattr(expires, "tzinfo", None) is not None:
        expires = expires.replace(tzinfo=None)
    if expires is None or datetime.utcnow() > expires:
        print(f"reset-password 400: expired otp for {email}")
        raise HTTPException(status_code=400, detail=PASSWORD_RESET_GENERIC_ERROR)

    await record_otp_verify_attempt(
        request=request,
        scope="password_reset",
        email=email,
        success=True,
    )

    # 🔒 Hash new password
    hashed_password = hash_password(payload.new_password)

    await users_collection.update_one(
        {"email": email, "role": "owner"},
        {
            "$set": {
                "password": hashed_password,
                "updated_at": datetime.utcnow()
            }
        }
    )

    # 🔒 Delete used OTP
    await otp_collection.delete_many({
        "email": email,
        "type": "password_reset"
    })

    log_device_fingerprint(request, "password_change", subject=email)

    await reset_auth_rate_limit(request, key=f"reset-password:{email}")

    return {
        "message": "Password reset successful"
    }

# ============================================================
# 2️⃣ START SMS MFA (ONLY if phone missing or manual trigger)
# ============================================================

@router.post("/start-sms-mfa")
async def start_sms_mfa(
    payload: StartSMSMFARequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    user = await users_collection.find_one({
        "email": payload.email.lower().strip(),
        "role": "owner"
    })

    if not user:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

    phone = user.get("phone")
    methods = normalize_mfa_methods(user)
    authorized_owner = await get_authorized_owner_for_email(
        payload.email.lower().strip(),
        authorization,
        request=request,
    )

    if not methods["sms"] and not authorized_owner:
        raise HTTPException(
            status_code=403,
            detail="SMS MFA is not linked. Sign in and enable it from Vault Settings."
        )

    if not phone and not payload.phoneNumber:
        return {
            "requires_phone": True,
            "message": "Phone number required"
        }

    if payload.phoneNumber:
        if not authorized_owner:
            raise HTTPException(
                status_code=403,
                detail="Sign in to Vault Settings before changing the SMS phone number."
            )

        try:
            phone = format_phone(payload.phoneNumber)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        await ensure_phone_available(
            phone,
            users_collection=users_collection,
            pending_signup_collection=pending_signup_collection,
            exclude_user_id=user["_id"],
            exclude_pending_email=user.get("email"),
        )

        await users_collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"phone": phone, "updated_at": datetime.utcnow()}}
        )

    skip_captcha = verify_mfa_challenge_token(
        payload.mfa_challenge_token,
        payload.email.lower().strip(),
    )

    try:
        sms_result = await send_otp_sms_secure(
            request=request,
            phone=phone,
            email=payload.email.lower().strip(),
            captcha_token=payload.captcha_token,
            session_id=payload.otp_session_id,
            skip_captcha=skip_captcha,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "requires_phone": False,
        "phone": phone,
        "message": (
            "OTP already sent"
            if sms_result.get("already_sent")
            else "OTP sent"
        ),
        "cooldown_seconds": sms_result.get(
            "cooldown_seconds", settings.OTP_PHONE_COOLDOWN_SECONDS
        ),
        "already_sent": bool(sms_result.get("already_sent")),
    }
# ============================================================
# START EMAIL MFA (login or Vault Settings)
# ============================================================

@router.post("/start-email-mfa")
async def start_email_mfa(
    payload: StartEmailMFARequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    email = payload.email.lower().strip()

    user = await users_collection.find_one({"email": email, "role": "owner"})
    if not user:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

    methods = normalize_mfa_methods(user)
    authorized_owner = await get_authorized_owner_for_email(
        email,
        authorization,
        request=request,
    )
    if not methods["email"] and not authorized_owner:
        raise HTTPException(
            status_code=403,
            detail="Email MFA is not linked. Sign in and enable it from Vault Settings."
        )

    skip_captcha = verify_mfa_challenge_token(payload.mfa_challenge_token, email)

    try:
        result = await send_email_otp_secure(
            request=request,
            email=email,
            captcha_token=payload.captcha_token,
            session_id=payload.otp_session_id,
            skip_captcha=skip_captcha,
            store_otp=_store_login_email_otp,
            rollback_otp=_rollback_login_email_otp,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to send verification email: {str(e)}",
        )

    return {
        "message": (
            f"Verification code already sent to {email}"
            if result.get("already_sent")
            else f"Verification code sent to {email}"
        ),
        "cooldown_seconds": result["cooldown_seconds"],
        "already_sent": bool(result.get("already_sent")),
    }

# ============================================================
# 3️⃣ RESEND OTP (CLEAN)
# ============================================================

@router.post("/resend-sms-mfa")
async def resend_sms_mfa(payload: ResendSignupSMSRequest, request: Request):
    email = payload.email.lower().strip()

    # pending signup first
    pending = await pending_signup_collection.find_one({
        "email": email,
        "mfa_method": "sms",
        "expires_at": {"$gt": datetime.utcnow()}
    })

    if pending:
        phone = pending.get("phone")
        if not phone:
            raise HTTPException(status_code=400, detail="Phone number not configured")

        try:
            sms_result = await send_otp_sms_secure(
                request=request,
                phone=phone,
                email=email,
                captcha_token=payload.captcha_token,
                session_id=payload.otp_session_id,
                # Pending signup SMS never requires Cloudflare
                skip_captcha=True,
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        return {
            "message": (
                "Signup OTP already sent"
                if sms_result.get("already_sent")
                else "Signup OTP resent successfully"
            ),
            "phone": phone,
            "cooldown_seconds": sms_result.get(
                "cooldown_seconds", settings.OTP_PHONE_COOLDOWN_SECONDS
            ),
            "already_sent": bool(sms_result.get("already_sent")),
        }

    # real user login flow
    user = await users_collection.find_one({
        "email": email,
        "role": "owner"
    })

    if not user:
        # Signup resend with no pending session — don't ask for Cloudflare
        if (payload.flow or "").lower().strip() == "signup":
            raise HTTPException(
                status_code=400,
                detail="No SMS signup in progress for this email. Start signup again.",
            )
        raise HTTPException(
            status_code=400,
            detail="No SMS signup in progress for this email. Start signup again.",
        )

    methods = normalize_mfa_methods(user)
    if not methods["sms"]:
        raise HTTPException(status_code=400, detail="SMS MFA not enabled")

    phone = user.get("phone")
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number not configured")

    try:
        sms_result = await send_otp_sms_secure(
            request=request,
            phone=phone,
            email=email,
            captcha_token=payload.captcha_token,
            session_id=payload.otp_session_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "message": (
            "OTP already sent"
            if sms_result.get("already_sent")
            else "OTP resent successfully"
        ),
        "phone": phone,
        "cooldown_seconds": sms_result.get(
            "cooldown_seconds", settings.OTP_PHONE_COOLDOWN_SECONDS
        ),
        "already_sent": bool(sms_result.get("already_sent")),
    }
# ============================================================
# 4️⃣ VERIFY OTP (FINAL LOGIN STEP)
# ============================================================

@router.post("/verify-sms-otp")
async def verify_sms_otp(
    payload: VerifySMSOTPRequest,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
):
    email = payload.email.lower().strip()
    otp = payload.code.strip()

    # first check pending signup
    pending = await pending_signup_collection.find_one({
        "email": email,
        "mfa_method": "sms",
        "expires_at": {"$gt": datetime.utcnow()}
    })

    if pending:
        phone = pending.get("phone")
        if not phone:
            raise HTTPException(status_code=400, detail="Phone number not configured")

        await ensure_verify_not_locked(phone, email)

        try:
            result = check_verification_code(phone, otp)
        except Exception as e:
            await record_verify_attempt(
                request=request,
                phone=phone,
                email=email,
                success=False,
                session_id=payload.otp_session_id,
                twilio_status=str(e),
            )

        if result.status != "approved":
            await record_verify_attempt(
                request=request,
                phone=phone,
                email=email,
                success=False,
                session_id=payload.otp_session_id,
                twilio_status=result.status,
            )

        await record_verify_attempt(
            request=request,
            phone=phone,
            email=email,
            success=True,
            session_id=payload.otp_session_id,
            twilio_status=result.status,
        )

        created_user = await create_real_user_from_pending(pending)
        return await issue_owner_session(response, created_user)

    await require_login_mfa_proof(
        email=email,
        mfa_challenge_token=payload.mfa_challenge_token,
        authorization=authorization,
        request=request,
        pending=None,
    )

    # otherwise normal login MFA
    user = await users_collection.find_one({
        "email": email,
        "role": "owner"
    })

    if not user:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

    methods = normalize_mfa_methods(user)
    authorized_owner = await get_authorized_owner_for_email(
        email, authorization, request=request
    )
    if not methods["sms"] and not authorized_owner:
        raise HTTPException(
            status_code=403,
            detail="SMS MFA is not linked. Sign in and enable it from Vault Settings."
        )

    phone = user.get("phone")
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number not configured")

    await ensure_verify_not_locked(phone, email)

    try:
        result = check_verification_code(phone, otp)
    except Exception as e:
        await record_verify_attempt(
            request=request,
            phone=phone,
            email=email,
            success=False,
            session_id=payload.otp_session_id,
            twilio_status=str(e),
        )

    if result.status != "approved":
        await record_verify_attempt(
            request=request,
            phone=phone,
            email=email,
            success=False,
            session_id=payload.otp_session_id,
            twilio_status=result.status,
        )

    await record_verify_attempt(
        request=request,
        phone=phone,
        email=email,
        success=True,
        session_id=payload.otp_session_id,
        twilio_status=result.status,
    )

    await users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "mfa_enabled": True,
                "primary_mfa": user.get("primary_mfa") or "sms",
                "mfa_methods.sms": True,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    updated_user = await users_collection.find_one({"_id": user["_id"]})
    if updated_user.get("role") == "owner":
        await record_owner_last_login(updated_user["email"])
    return await issue_owner_session(response, updated_user)
# ============================================================
# 5️⃣ LINK PHONE (ENABLE SMS MFA)
# ============================================================

@router.post("/link-sms")
async def link_sms(
    payload: PhoneRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)

    user = await users_collection.find_one({
        "email": decoded["sub"],
        "role": "owner"
    })

    if not user:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

    try:
        phone = format_phone(payload.phoneNumber)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await ensure_phone_available(
        phone,
        users_collection=users_collection,
        pending_signup_collection=pending_signup_collection,
        exclude_user_id=user["_id"],
        exclude_pending_email=user.get("email"),
    )

    await users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "phone": phone,
                "mfa_enabled": True,
                "primary_mfa": user.get("primary_mfa") or "sms",
                "mfa_methods.sms": True,
                "updated_at": datetime.utcnow()
            }
        }
    )

    return {"message": "SMS MFA enabled successfully"}

    
@router.post("/resume-pending-signup")
async def resume_pending_signup(payload: EmailRequest, request: Request):
    email = payload.email.lower().strip()

    await enforce_auth_rate_limit(request, key=f"resume-signup:{email}")

    pending = await pending_signup_collection.find_one({
        "email": email,
        "expires_at": {"$gt": datetime.utcnow()}
    })

    if not pending:
        return {"pending": False, "message": PENDING_SIGNUP_GENERIC}

    method = pending.get("mfa_method")

    if method == "authenticator":
        secret = read_pending_totp_secret(pending)
        if not secret:
            raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

        uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name=email,
            issuer_name="Orderly Affairs"
        )
        qr = qrcode.make(uri)
        buf = BytesIO()
        qr.save(buf, format="PNG")
        img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        return {
            "pending": True,
            "method": "authenticator",
            "email": email,
            "qrCodeUrl": f"data:image/png;base64,{img_base64}",
        }

    if method == "email":
        return {
            "pending": True,
            "method": "email",
            "email": email,
        }

    if method == "sms":
        return {
            "pending": True,
            "method": "sms",
            "email": email,
        }

    raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)
