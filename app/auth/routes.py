from fastapi import APIRouter, Request, HTTPException, Header, Depends, Response, Body
from typing import List, Union

from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
from random import randint
from app.auth.notification_prefs import (
    get_owner_notification_prefs,
    merge_notification_prefs_patch,
    vault_push_session_payload,
)
from app.auth.death_detection import (
    record_nextkin_last_login,
    record_owner_last_login,
    user_is_returning_login,
    user_is_returning_for_session,
)
from app.auth.service import record_pending_death_report
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
from app.security.token_resolver import decode_access_token, decode_owner_or_nok_token
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

class CollaboratorPasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str

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

class RevealNextKinPasswordRequest(BaseModel):
    password: str | None = None
    mfa_challenge_token: str | None = None
    step_up_token: str | None = None


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
from app.auth.family_schemas import (
    FamilyCreateRequest,
    FamilyRoleAreasUpdateRequest,
    FamilyUpdateRequest,
)

# ---- Next-of-Kin ----
class NextKinUpdateRequest(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = None
    relationship: str | None = None
    phone_number: str | None = None
    access_level: str | None = None
    authorized_sections: list[str] | None = None
    portal_role: str | None = None
    immediate_access: bool | None = None
    nok_letter_received: bool | None = None
    master_password: str | None = None
    password_card_generated: bool | None = None
    card_storage_location: str | None = None
    key_bag_location: str | None = None
    documents_bag_location: str | None = None
    special_instructions: str | None = None


class DeathCertificateAuthorizationAgreeRequest(BaseModel):
    agreed: bool
    signature_name: str


class NextKinClaimStartRequest(BaseModel):
    token: str


class NextKinClaimCompleteRequest(BaseModel):
    token: str
    password: str


class ApproveLivingAccessRequest(BaseModel):
    password: str | None = None
    confirm_password: str | None = None
    mfa_challenge_token: str | None = None
    step_up_token: str | None = None

    def step_up_password(self) -> str:
        return (self.password or self.confirm_password or "").strip()


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
    return await get_authorized_user_for_email(
        email,
        authorization,
        request=request,
        roles=("owner",),
    )


async def get_authorized_user_for_email(
    email: str,
    authorization: str | None,
    request: Request | None = None,
    *,
    roles: tuple[str, ...] = ("owner", "nextkin"),
) -> dict | None:
    """Return the signed-in user when the session matches the email (owner or NOK)."""
    if request is None:
        return None

    try:
        decoded = decode_owner_or_nok_token(request, authorization)
    except HTTPException:
        return None

    role = decoded.get("role") or "owner"
    if role not in roles:
        return None

    user = None
    if role == "nextkin":
        try:
            user = await users_collection.find_one(
                {"_id": ObjectId(decoded["sub"]), "role": "nextkin"}
            )
        except Exception:
            user = None
        if not user:
            # fallback if older tokens used email as sub
            user = await users_collection.find_one(
                {"email": decoded.get("email") or decoded["sub"], "role": "nextkin"}
            )
    else:
        user = await users_collection.find_one(
            {
                "email": decoded["sub"],
                "role": role if role in roles else "owner",
            }
        )

    if not user:
        return None

    session_email = (user.get("email") or "").lower().strip()
    if session_email != email.lower().strip():
        return None

    return user


async def issue_session_for_user(response: Response, user: dict) -> dict:
    """Issue owner or NOK/family cookie session based on role."""
    role = user.get("role")
    if role == "nextkin":
        returning = await record_nextkin_last_login(str(user["_id"]))
        if not returning:
            from app.auth.immediate_access_grant import mark_living_access_active

            await mark_living_access_active(user["_id"])
        from app.auth.access_types import is_family_collaborator

        if not is_family_collaborator(user):
            await maybe_notify_owner_nextkin_first_access(nextkin=user)
        session = await issue_nok_session(response, user)
        session["returning_user"] = returning
        return session
    if role == "owner":
        returning = await record_owner_last_login(user["email"])
        session = await issue_owner_session(response, user)
        session["returning_user"] = returning
        session["full_name"] = user.get("full_name")
        return session
    raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)


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

    authorized = await get_authorized_user_for_email(
        email,
        authorization,
        request=request,
    )
    if authorized:
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
    plain = (password or "").strip()

    # Owners store `password`; family/NOK accounts often use `password_hash`.
    # Try each independently so a stale unused field cannot block the real hash.
    if plain:
        for key in ("password", "password_hash"):
            stored = user.get(key) or ""
            if isinstance(stored, str) and stored and verify_password(plain, stored):
                return

    if verify_mfa_challenge_token(mfa_challenge_token, email):
        return

    if verify_step_up_token(step_up_token, email):
        return

    if plain:
        raise HTTPException(
            status_code=403,
            detail=(
                "Incorrect account password. Use the password you sign in to "
                "Orderly Affairs with — not this person's login password."
            ),
        )

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
    del plain_password
    if nextkin.get("immediate_access"):
        return
    from app.auth.immediate_access_grant import begin_owner_immediate_access_grant

    await begin_owner_immediate_access_grant(nextkin=nextkin, owner=owner)


async def _approve_and_notify_if_needed(
    nextkin: dict,
    owner: dict,
    approved: bool = True,
    plain_password: str | None = None,
):
    del plain_password
    from app.auth.immediate_access_grant import (
        begin_owner_immediate_access_grant,
        cancel_pending_immediate_access,
    )

    was_live = bool(nextkin.get("immediate_access"))
    was_pending = bool(nextkin.get("immediate_access_pending"))

    if approved:
        if was_live or was_pending:
            return
        await begin_owner_immediate_access_grant(nextkin=nextkin, owner=owner)
        return

    if not was_live and not was_pending:
        return

    await cancel_pending_immediate_access(nextkin["_id"])
    if nextkin.get("access_timing") == "immediate" or was_live or was_pending:
        await users_collection.update_one(
            {"_id": nextkin["_id"]},
            {
                "$set": {
                    "access_revoked": True,
                    "immediate_access": False,
                    "updated_at": datetime.utcnow(),
                }
            },
        )

    if was_live:
        try:
            await send_nextkin_email(
                event=NextKinEmailEvent.ACCESS_REVOKED,
                nextkin=nextkin,
                owner=owner,
            )
        except Exception as e:
            print("⚠️ Next-of-Kin access notification email failed:", e)
    try:
        from app.notifications.owner_nok_alerts import notify_owner_revoke_succeeded

        await notify_owner_revoke_succeeded(owner=owner)
    except Exception as e:
        print("⚠️ Owner revoke confirmation email failed:", e)


async def maybe_notify_owner_nextkin_first_access(*, nextkin: dict) -> None:
    """Email the vault owner once, after this next of kin's first completed login."""
    from app.auth.access_types import is_family_collaborator
    from app.auth.collaborator_security import (
        owner_nok_first_access_claim_filter,
        should_send_owner_access_alert,
    )
    from bson import ObjectId
    from bson.errors import InvalidId

    nextkin_oid = nextkin.get("_id")
    if not nextkin_oid:
        return
    fresh = await users_collection.find_one({"_id": nextkin_oid, "role": "nextkin"})
    if not fresh or is_family_collaborator(fresh):
        return
    if not should_send_owner_access_alert(fresh):
        return

    owner_id = fresh.get("owner_id")
    nextkin_id = str(fresh.get("_id") or "")
    if not owner_id or not nextkin_id:
        return
    try:
        owner_oid = ObjectId(str(owner_id))
    except (InvalidId, TypeError):
        return

    now = datetime.utcnow()
    already = {
        str(item) for item in (
            (
                await users_collection.find_one(
                    {"_id": owner_oid, "role": "owner"},
                    {"nok_first_access_alert_ids": 1},
                )
                or {}
            ).get("nok_first_access_alert_ids")
            or []
        )
    }
    if nextkin_id in already:
        await users_collection.update_one(
            {"_id": nextkin_oid, "role": "nextkin"},
            {"$set": {"owner_access_alert_sent_at": now, "updated_at": now}},
        )
        return

    owner = await users_collection.find_one_and_update(
        owner_nok_first_access_claim_filter(
            owner_id=owner_oid,
            nextkin_id=nextkin_id,
        ),
        {
            "$addToSet": {"nok_first_access_alert_ids": nextkin_id},
            "$set": {"updated_at": now},
        },
    )
    if not owner:
        await users_collection.update_one(
            {"_id": nextkin_oid, "role": "nextkin"},
            {"$set": {"owner_access_alert_sent_at": now, "updated_at": now}},
        )
        return

    await users_collection.update_one(
        {"_id": nextkin_oid, "role": "nextkin"},
        {"$set": {"owner_access_alert_sent_at": now, "updated_at": now}},
    )

    from app.notifications.owner_nok_alerts import notify_owner_nextkin_signed_in

    await notify_owner_nextkin_signed_in(owner=owner, nextkin=fresh)

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

    from app.auth.deleted_account_registry import assert_identity_not_deleted

    await assert_identity_not_deleted(email=email)

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
        await assert_identity_not_deleted(email=email, phone=phone)

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

    returning = await record_owner_last_login(email)
    log_device_fingerprint(request, "login_success", subject=email)
    session = await issue_owner_session(response, user)
    session["email"] = email
    session["returning_user"] = returning
    session["full_name"] = user.get("full_name")
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

    from app.auth.access_types import (
        require_collaborator_login_portal,
        resolve_access_type,
    )

    require_collaborator_login_portal(user, data.get("portal"))

    await reset_auth_rate_limit(request, key=f"nok-login:{email}")

    if user.get("access_revoked") or not user.get("immediate_access", False):
        raise HTTPException(status_code=403, detail=NOK_LOGIN_GENERIC)

    from app.auth.immediate_access_grant import expire_unused_living_access_if_due

    if await expire_unused_living_access_if_due(user):
        raise HTTPException(status_code=403, detail=NOK_LOGIN_GENERIC)

    owner = await users_collection.find_one(
        {"_id": ObjectId(user["owner_id"]), "role": "owner"}
    )

    if owner and owner.get("billing", {}).get("status") == "blocked":
        raise HTTPException(status_code=403, detail=NOK_LOGIN_GENERIC)

    # Step-up MFA for NOK / family (email OTP and/or authenticator).
    # Every NOK session requires a second factor. If no MFA method is enrolled,
    # force email OTP (and encourage enrollment). Full Kit keeps the same path.
    from app.auth.session_manager import is_full_kit_access

    methods = normalize_mfa_methods(user)
    has_mfa = bool(user.get("mfa_enabled") or any(methods.values()))
    force_email_mfa = not any(methods.values())
    force_full_kit_mfa = is_full_kit_access(user) and force_email_mfa

    if has_mfa or force_email_mfa:
        if has_mfa and not any(methods.values()):
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
            # Cleared broken MFA flags — still require email OTP for all NOK.
            has_mfa = False

        billing = (owner or {}).get("billing", {}) if owner else {}
        if force_email_mfa:
            mfa_response = {
                "message": "Password verified",
                "mfa_required": True,
                "mfa_setup_recommended": True,
                "method": "email",
                "methods": ["email"],
                "mfa_methods": {
                    "email": True,
                    "authenticator": False,
                    "sms": False,
                },
                "email": email,
                "phone": user.get("phone"),
                "otp_sent": False,
            }
            preferred = "email"
        else:
            mfa_response = mfa_login_response(user, billing)
            preferred = mfa_response.get("method")

        mfa_response["mfa_challenge_token"] = create_mfa_challenge_token(email)
        mfa_response["step_up_token"] = create_step_up_token(email)
        mfa_response["portal"] = resolve_access_type(user)
        mfa_response["access_type"] = resolve_access_type(user)
        if force_full_kit_mfa:
            mfa_response["full_kit_mfa_required"] = True
        elif force_email_mfa:
            mfa_response["nok_mfa_required"] = True

        otp_sent, cooldown_seconds, otp_error = await _trigger_login_mfa_otp(
            request=request,
            user=user,
            method=preferred,
            email=email,
        )
        mfa_response["otp_sent"] = otp_sent
        if cooldown_seconds is not None:
            mfa_response["cooldown_seconds"] = cooldown_seconds
        if otp_error:
            mfa_response["otp_error"] = otp_error

        return mfa_response

    return await issue_session_for_user(response, user)


@router.post("/reveal-nextkin-password/{nextkin_id}")
async def reveal_nextkin_password(
    nextkin_id: str,
    request: Request,
    payload: RevealNextKinPasswordRequest,
    authorization: str | None = Header(default=None),
):
    """
    Owner (or family Admin+) one-shot reveal of a NOK/family master password.

    Requires step-up (account password / recent MFA / step_up_token).
    List endpoints no longer return plaintext. Call this when printing a card
    or showing the eye toggle — rate-limited per owner.
    """
    decoded = decode_access_token(request, authorization)
    from app.auth.family_access import DASHBOARD_AREA_SECTION2_NOK
    from app.auth.vault_actor import require_owner_or_family

    actor, owner = await require_owner_or_family(
        decoded,
        perm="can_manage_nextkin",
        area_id=DASHBOARD_AREA_SECTION2_NOK,
        detail="Only the owner or a family Admin+ can reveal Next-of-Kin passwords",
    )

    require_step_up_auth(
        user=actor,
        password=payload.password,
        mfa_challenge_token=payload.mfa_challenge_token,
        step_up_token=payload.step_up_token,
    )

    await enforce_auth_rate_limit(
        request,
        key=f"reveal-nok-pw:{owner['_id']}",
    )

    try:
        oid = ObjectId(nextkin_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Next-of-Kin id") from exc

    nk = await users_collection.find_one(
        {
            "_id": oid,
            "role": "nextkin",
            "owner_id": str(owner["_id"]),
        }
    )
    if not nk:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found")

    profile = load_nextkin_profile(dict(nk)) or dict(nk)
    password = str(profile.get("master_password") or "").strip()
    if not password:
        raise HTTPException(
            status_code=404,
            detail="No master password on file. Edit the person to set a new one.",
        )

    return {
        "success": True,
        "nextkin_id": str(nk["_id"]),
        "email": nk.get("email"),
        "master_password": password,
        "message": "Show this password privately, then clear it from the screen.",
    }


# ============================================================
# 4️⃣ OWNER CREATES NEXT-OF-KIN ACCOUNT (FINAL VERSION)
# ============================================================
# === helper (place near top, after imports) ===
def generate_temp_password(length: int = 12) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


@router.get("/death-certificate-authorization")
async def get_death_certificate_authorization(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Vault copy of the death-certificate authorization + owner signature status."""
    decoded = decode_owner_or_nok_token(request, authorization)
    from app.auth.access_types import is_family_collaborator
    from app.auth.portal_roles import resolve_dashboard_permissions
    from app.auth.vault_actor import resolve_actor, resolve_vault_owner
    from app.legal.death_certificate_authorization import (
        agreement_status,
        document_payload,
    )

    actor = await resolve_actor(decoded)
    if not actor:
        raise HTTPException(status_code=401, detail="Unauthorized")

    can_sign = False
    if actor.get("role") == "owner":
        owner = actor
        can_sign = True
    elif is_family_collaborator(actor):
        if not actor.get("immediate_access", False):
            raise HTTPException(status_code=403, detail="Access not approved")
        owner = await resolve_vault_owner(actor)
        can_sign = bool(resolve_dashboard_permissions(actor).get("can_manage_nextkin"))
    elif actor.get("role") == "nextkin":
        owner = await resolve_vault_owner(actor)
        can_sign = False
    else:
        raise HTTPException(status_code=403, detail="Forbidden")

    return {
        **document_payload(),
        **agreement_status(owner),
        "can_sign": can_sign,
    }


@router.post("/death-certificate-authorization")
async def agree_death_certificate_authorization(
    payload: DeathCertificateAuthorizationAgreeRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)
    from app.auth.family_access import DASHBOARD_AREA_SECTION2_NOK
    from app.auth.vault_actor import require_owner_or_family
    from app.legal.death_certificate_authorization import (
        SIGNATURE_REQUIRED,
        agreement_set_fields,
        agreement_status,
        document_payload,
    )

    _, owner = await require_owner_or_family(
        decoded,
        perm="can_manage_nextkin",
        area_id=DASHBOARD_AREA_SECTION2_NOK,
        detail="Only the owner or a family Admin+ can sign this authorization",
    )
    if not payload.agreed:
        raise HTTPException(
            status_code=400,
            detail="You must check the box to agree to this Authorization",
        )
    signature = (payload.signature_name or "").strip()
    if not signature:
        raise HTTPException(status_code=400, detail=SIGNATURE_REQUIRED)

    await users_collection.update_one(
        {"_id": owner["_id"]},
        {"$set": agreement_set_fields(signature)},
    )
    updated = await users_collection.find_one({"_id": owner["_id"]})
    return {
        **document_payload(),
        **agreement_status(updated),
        "can_sign": True,
    }


def _mask_email(email: str) -> str:
    value = (email or "").strip()
    if "@" not in value:
        return "hidden"
    local, _, domain = value.partition("@")
    if len(local) <= 1:
        shown = "*"
    else:
        shown = local[0] + "***"
    return f"{shown}@{domain}"


@router.post("/claim-nextkin/start")
async def start_nextkin_claim(payload: NextKinClaimStartRequest):
    """Validate a one-time vault-unlock claim link (no session yet)."""
    from app.auth.claim_tokens import claim_is_expired, hash_claim_token

    token = (payload.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="This access link is invalid")

    user = await users_collection.find_one(
        {"role": "nextkin", "claim_token_hash": hash_claim_token(token)}
    )
    if not user or user.get("claim_token_used_at") or claim_is_expired(
        user.get("claim_token_expires_at")
    ):
        raise HTTPException(
            status_code=400,
            detail="This access link is invalid or has expired",
        )
    from app.auth.didit import DIDIT_APPROVED, claims_require_didit

    if claims_require_didit() and user.get("didit_status") != DIDIT_APPROVED:
        raise HTTPException(
            status_code=400,
            detail="Identity verification is not complete for this access link",
        )

    return {
        "email": _mask_email(str(user.get("email") or "")),
        "full_name": user.get("full_name"),
        "relationship": user.get("relationship"),
    }


@router.post("/claim-nextkin/complete")
async def complete_nextkin_claim(
    payload: NextKinClaimCompleteRequest,
    response: Response,
):
    """Consume the one-time claim link, set a password, and start the NOK session."""
    from app.auth.access_types import is_family_collaborator
    from app.auth.claim_tokens import claim_is_expired, hash_claim_token
    from app.auth.collaborator_security import password_changed_fields
    from app.auth.session_manager import issue_nok_session
    from app.security.password_handler import hash_password

    token = (payload.token or "").strip()
    password = (payload.password or "").strip()
    confirm = (payload.confirm_password or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="This access link is invalid")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if password != confirm:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    token_hash = hash_claim_token(token)
    user = await users_collection.find_one(
        {"role": "nextkin", "claim_token_hash": token_hash}
    )
    if not user or user.get("claim_token_used_at") or claim_is_expired(
        user.get("claim_token_expires_at")
    ):
        raise HTTPException(
            status_code=400,
            detail="This access link is invalid or has expired",
        )
    if is_family_collaborator(user):
        raise HTTPException(status_code=400, detail="This access link is invalid")

    from app.auth.didit import DIDIT_APPROVED, claims_require_didit

    if claims_require_didit() and user.get("didit_status") != DIDIT_APPROVED:
        raise HTTPException(
            status_code=400,
            detail="Identity verification is not complete for this access link",
        )

    now = datetime.utcnow()
    await users_collection.update_one(
        {"_id": user["_id"], "claim_token_hash": token_hash},
        {
            "$set": {
                "password_hash": hash_password(password),
                "immediate_access": True,
                "must_enroll_mfa": True,
                "claim_token_used_at": now,
                "updated_at": now,
                **password_changed_fields(),
            },
            "$unset": {
                "claim_token_hash": "",
                "claim_token_expires_at": "",
                "master_password": "",
            },
        },
    )
    refreshed = await users_collection.find_one({"_id": user["_id"]})
    if not refreshed:
        raise HTTPException(status_code=400, detail="Could not complete access")
    return await issue_nok_session(response, refreshed)


@router.post("/create-nextkin")
async def create_nextkin(
    payload: Union[NextKinCreateRequest, list[NextKinCreateRequest]],
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Create one or many Next-of-Kin users. Same endpoint handles single or list payloads."""

    decoded = decode_access_token(request, authorization)
    from app.auth.family_access import DASHBOARD_AREA_SECTION2_NOK
    from app.auth.vault_actor import require_owner_or_family

    _, owner = await require_owner_or_family(
        decoded,
        perm="can_manage_nextkin",
        area_id=DASHBOARD_AREA_SECTION2_NOK,
        detail="Only the owner or a family Admin+ with Section 2 access can create Next-of-Kin",
    )

    from app.auth.access_types import (
        ACCESS_TYPE_NEXTKIN,
        NEXTKIN_ACCESS_MONGO_FILTER,
        validate_nok_authorized_sections,
    )

    count = await users_collection.count_documents({
        "owner_id": str(owner["_id"]),
        "role": "nextkin",
        "$and": [NEXTKIN_ACCESS_MONGO_FILTER],
    })

    enforce_usage(owner, "nextkin", count)
    # small inner util to avoid duplication
    async def _create_one(req: NextKinCreateRequest):
        from app.auth.nextkin_validation import prepare_nextkin_create_fields

        # Re-check cap for bulk creates
        current = await users_collection.count_documents({
            "owner_id": str(owner["_id"]),
            "role": "nextkin",
            "$and": [NEXTKIN_ACCESS_MONGO_FILTER],
        })
        try:
            enforce_usage(owner, "nextkin", current)
        except HTTPException as exc:
            return {
                "email": str(getattr(req, "email", "") or "").lower(),
                "status": "error",
                "error": exc.detail,
            }

        try:
            normalized = prepare_nextkin_create_fields(req)
            sections = validate_nok_authorized_sections(
                normalized["access_level"],
                normalized.get("authorized_sections"),
            )
            normalized["authorized_sections"] = sections
        except ValueError as exc:
            return {
                "email": str(getattr(req, "email", "") or "").lower(),
                "status": "error",
                "error": str(exc),
            }
        except HTTPException as exc:
            return {
                "email": str(getattr(req, "email", "") or "").lower(),
                "status": "error",
                "error": exc.detail,
            }

        email = normalized["email"]

        if not req.immediate_access:
            from app.legal.death_certificate_authorization import (
                PERSON_CONFIRM_REQUIRED,
                SIGNATURE_REQUIRED,
                OWNER_RECORD_KEY,
                agreement_set_fields,
                owner_has_death_certificate_authorization,
            )

            if not req.death_certificate_authorization_agreed:
                return {
                    "email": email,
                    "status": "error",
                    "error": PERSON_CONFIRM_REQUIRED,
                }
            if not owner_has_death_certificate_authorization(owner):
                signature = (
                    req.death_certificate_authorization_signature or ""
                ).strip()
                if not signature:
                    return {
                        "email": email,
                        "status": "error",
                        "error": SIGNATURE_REQUIRED,
                    }
                fields = agreement_set_fields(signature)
                await users_collection.update_one(
                    {"_id": owner["_id"]},
                    {"$set": fields},
                )
                owner[OWNER_RECORD_KEY] = fields[OWNER_RECORD_KEY]

        # 2️⃣ Prevent duplicate
        existing = await users_collection.find_one({"email": email})
        if existing:
            return {
                "email": email,
                "status": "error",
                "error": "Next-of-Kin already exists"
            }

        # 3️⃣ Immediate access needs a login password. Upon-death NOK
        # wait for death verification — no master password is stored now.
        plain_password = None
        if req.immediate_access:
            plain_password = req.master_password or generate_temp_password()

        new_nok = {
            "email": email,
            "full_name": normalized["full_name"],
            "relationship": normalized["relationship"],
            "phone_number": req.phone_number,

            "access_level": normalized["access_level"],
            "authorized_sections": normalized["authorized_sections"] or [],
            "access_type": ACCESS_TYPE_NEXTKIN,
            "immediate_access": False,
            "access_timing": "immediate" if req.immediate_access else "upon_death",
            "access_revoked": False,
            "nok_letter_received": (
                bool(req.nok_letter_received) if not req.immediate_access else False
            ),

            "password_card_generated": False,
            "card_storage_location": req.card_storage_location,
            "key_bag_location": req.key_bag_location,
            "documents_bag_location": req.documents_bag_location,
            "special_instructions": req.special_instructions,

            "role": "nextkin",
            "owner_id": str(owner["_id"]),
            "verified": True,
            "mfa_enabled": False,
            "must_change_password": True,
            "must_enroll_mfa": True,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        if not req.immediate_access:
            new_nok["death_certificate_person_confirmed_at"] = datetime.utcnow()
        if plain_password:
            new_nok["master_password"] = plain_password
            new_nok["password_hash"] = hash_password(plain_password)
            new_nok["password_card_generated"] = True

        stored_nok = prepare_nextkin_profile_for_storage(new_nok)
        insert_res = await users_collection.insert_one(stored_nok)
        new_id = insert_res.inserted_id

        nextkin = load_nextkin_profile(
            await users_collection.find_one({"_id": new_id})
        )

        # Enrollment only. Living release is a separate owner action
        # (confirm + password). The NOK login email is sent immediately.
        await send_nextkin_email(
            event=NextKinEmailEvent.CREATED,
            nextkin=nextkin,
            owner=owner,
            plain_password=None,
        )

        return {
            "id": str(new_id),
            "email": email,
            "full_name": req.full_name,
            "relationship": req.relationship,
            "status": "ok",
            "message": (
                f"Next-of-Kin '{req.full_name}' created successfully. "
                "Click Release Access after they accept the invite."
                if req.immediate_access
                else f"Next-of-Kin '{req.full_name}' created successfully."
            ),
            "master_password": plain_password,
            "temp_password_sent": False,
            "immediate_access_pending": False,
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
        "temp_password_sent": bool(res.get("temp_password_sent")),
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
    decoded = decode_owner_or_nok_token(request, authorization)
    from app.auth.family_access import (
        DASHBOARD_AREA_OVERVIEW,
        DASHBOARD_AREA_SECTION2_NOK,
        family_has_dashboard_area,
    )
    from app.auth.portal_roles import resolve_dashboard_permissions
    from app.auth.vault_actor import (
        require_owner_or_family,
        require_owner_or_family_reader,
    )

    if decoded.get("role") == "owner":
        _, owner = await require_owner_or_family(decoded)
    else:
        actor, owner = await require_owner_or_family_reader(
            decoded,
            detail="Family access required to view Next-of-Kin",
        )
        perms = resolve_dashboard_permissions(actor)
        allowed = (
            bool(perms.get("can_manage_nextkin"))
            or family_has_dashboard_area(actor, DASHBOARD_AREA_SECTION2_NOK)
            or family_has_dashboard_area(actor, "2")
            or family_has_dashboard_area(actor, "3")
            or family_has_dashboard_area(actor, DASHBOARD_AREA_OVERVIEW)
        )
        if not allowed:
            raise HTTPException(
                status_code=403,
                detail=(
                    "No access to Next-of-Kin list. Ask the owner to grant "
                    "Section 2, Section 3 (letters), or overview."
                ),
            )

    from app.auth.access_types import NEXTKIN_ACCESS_MONGO_FILTER

    nextkins = users_collection.find({
        "owner_id": str(owner["_id"]),
        "role": "nextkin",
        "$and": [NEXTKIN_ACCESS_MONGO_FILTER],
    })
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
            "access_type": "nextkin",
            "immediate_access": nk.get("immediate_access", False),
            "immediate_access_pending": bool(nk.get("immediate_access_pending")),
            "immediate_access_email_at": nk.get("immediate_access_email_at"),
            "access_timing": nk.get("access_timing"),
            "living_access_state": nk.get("living_access_state"),
            "nok_letter_received": nk.get("nok_letter_received", False),

            "password_card_generated": nk.get("password_card_generated"),
            "has_master_password": bool(
                nk.get("password_hash") or nk.get("master_password")
            ),
            # Do not return plaintext master_password on list.
            # Owner reveals via POST /auth/reveal-nextkin-password/{id}.
            "master_password": "",
            "card_storage_location": nk.get("card_storage_location"),
            "key_bag_location": nk.get("key_bag_location"),
            "documents_bag_location": nk.get("documents_bag_location"),
            "special_instructions": nk.get("special_instructions"),

            "created_at": nk.get("created_at"),
            "updated_at": nk.get("updated_at"),
        })

    return results


@router.get("/portal-roles")
async def list_portal_roles(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Role catalog for owner Access Management UI."""
    decode_access_token(request, authorization)
    from app.auth.portal_roles import list_portal_roles_for_api

    return {"roles": list_portal_roles_for_api()}


@router.get("/section-footprints")
async def get_section_footprints(
    request: Request,
    authorization: str | None = Header(default=None),
    limit: int = 50,
    section_id: str | None = None,
):
    """Audit trail: who last updated vehicles, insurance, etc."""
    decoded = decode_access_token(request, authorization)
    from app.auth.vault_actor import require_owner_or_family_reader

    _, owner = await require_owner_or_family_reader(
        decoded,
        detail="Only the owner or family collaborators can view footprints",
    )

    from app.repositories.section_repository import SectionRepository

    owner_id = str(owner["_id"])
    history = await SectionRepository.list_footprints(
        owner_id, limit=limit, section_id=section_id
    )
    latest = await SectionRepository.latest_by_section(owner_id)
    latest_subsections = await SectionRepository.latest_by_subsection(owner_id)
    if section_id:
        sid = str(section_id)
        latest_subsections = [
            row
            for row in latest_subsections
            if str(row.get("section_id") or "") == sid
        ]
    return {
        "latest": latest,
        "latest_subsections": latest_subsections,
        "history": history,
    }


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
    from app.auth.family_access import DASHBOARD_AREA_SECTION2_NOK
    from app.auth.vault_actor import require_owner_or_family

    _, owner = await require_owner_or_family(
        decoded,
        perm="can_manage_nextkin",
        area_id=DASHBOARD_AREA_SECTION2_NOK,
        detail="Only the owner or a family Admin+ with Section 2 access can update Next-of-Kin",
    )

    nextkin = await users_collection.find_one(
        {"_id": ObjectId(nextkin_id), "role": "nextkin", "owner_id": str(owner["_id"])}
    )
    if not nextkin:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found or not linked to this owner")

    from app.auth.access_types import (
        is_nextkin_collaborator,
        validate_nok_authorized_sections,
    )

    if not is_nextkin_collaborator(nextkin):
        raise HTTPException(
            status_code=400,
            detail="This person is a family collaborator — manage them in Vault Settings",
        )

    current_profile = load_nextkin_profile(dict(nextkin)) or dict(nextkin)
    previous_password = current_profile.get("master_password")

    # ✅ Only update provided fields
    update_data = {k: v for k, v in payload.dict().items() if v is not None}

    # NOK never stores write portal roles
    update_data.pop("portal_role", None)
    update_data["access_type"] = "nextkin"

    if "authorized_sections" in update_data or "access_level" in update_data:
        level = update_data.get("access_level") or current_profile.get("access_level")
        sections = update_data.get(
            "authorized_sections",
            current_profile.get("authorized_sections"),
        )
        update_data["authorized_sections"] = validate_nok_authorized_sections(
            level, sections
        )

    if "immediate_access" in update_data:
        # Living access is granted only via Approve (NOK is emailed immediately).
        update_data.pop("immediate_access")

    password_changed = False
    new_password = (payload.master_password or "").strip() or None
    if new_password and new_password != (previous_password or ""):
        password_changed = True
        update_data["password_hash"] = hash_password(new_password)
        update_data["master_password"] = new_password
        update_data["must_change_password"] = True
    elif "master_password" in update_data and not new_password:
        update_data.pop("master_password", None)

    if not update_data:
        raise HTTPException(status_code=400, detail="No valid fields provided to update")

    merged_profile = dict(current_profile)
    merged_profile.update(update_data)
    merged_profile.pop("portal_role", None)
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

    update_doc: dict = {"$set": stored_profile, "$unset": {"portal_role": ""}}
    if unset:
        update_doc["$unset"].update(unset)

    await users_collection.update_one({"_id": ObjectId(nextkin_id)}, update_doc)

    password_email_sent = False
    if password_changed and new_password:
        updated_nextkin = load_nextkin_profile(
            await users_collection.find_one({"_id": ObjectId(nextkin_id)})
        )
        # Only email password changes to people who already have immediate access.
        if updated_nextkin and updated_nextkin.get("immediate_access"):
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
    from app.auth.family_access import DASHBOARD_AREA_SECTION2_NOK
    from app.auth.vault_actor import require_owner_or_family

    _, owner = await require_owner_or_family(
        decoded,
        perm="can_manage_nextkin",
        area_id=DASHBOARD_AREA_SECTION2_NOK,
        detail="Only the owner or a family Admin+ with Section 2 access can delete Next-of-Kin",
    )

    # 3️⃣ Find and delete nextkin
    nextkin = await users_collection.find_one(
        {"_id": ObjectId(nextkin_id), "role": "nextkin", "owner_id": str(owner["_id"])}
    )
    if not nextkin:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found or not linked to this owner")

    from app.auth.access_types import is_nextkin_collaborator

    if not is_nextkin_collaborator(nextkin):
        raise HTTPException(
            status_code=400,
            detail="This person is a family collaborator — manage them in Vault Settings",
        )

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


# ============================================================
# FAMILY COLLABORATORS (Vault Settings — separate from Section 2)
# ============================================================
@router.post("/create-family")
async def create_family(
    payload: FamilyCreateRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    from app.auth.family_routes import create_family_member

    return await create_family_member(
        payload,
        request,
        authorization,
        generate_password=generate_temp_password,
    )


@router.get("/my-family")
async def get_my_family(
    request: Request,
    authorization: str | None = Header(default=None),
):
    from app.auth.family_routes import list_family_members

    return await list_family_members(request, authorization)


@router.put("/update-family/{family_id}")
async def update_family(
    family_id: str,
    payload: FamilyUpdateRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    from app.auth.family_routes import update_family_member

    return await update_family_member(family_id, payload, request, authorization)


@router.delete("/delete-family/{family_id}")
async def delete_family(
    family_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    from app.auth.family_routes import delete_family_member

    return await delete_family_member(family_id, request, authorization)


@router.get("/family-role-areas")
async def get_family_role_areas(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Global default vault areas per portal role (owner kit)."""
    from app.auth.family_routes import get_family_role_areas as _get

    return await _get(request, authorization)


@router.put("/family-role-areas")
async def put_family_role_areas(
    payload: FamilyRoleAreasUpdateRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Increase/reduce access areas for Viewer–Super Admin globally."""
    from app.auth.family_routes import update_family_role_areas

    return await update_family_role_areas(payload, request, authorization)


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

    from app.security.vault_principals import require_nok_principal

    require_nok_principal(nextkin)

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
            "death_report_pending": bool(owner.get("death_report_pending")),
        }

    from app.auth.claimant_roles import public_claimant_flags
    from app.auth.didit import session_public_payload
    from app.auth.ssdmf import public_death_verification

    claimant = public_claimant_flags(nextkin)
    pending = bool((owner or {}).get("death_report_pending"))
    deceased = (owner or {}).get("owner_status") == "deceased"

    return {
        "full_access": full_access,
        "authorized_sections": "all" if full_access else nextkin.get("authorized_sections", []),
        "access_level": access_level,
        "access_type": nextkin.get("access_type") or "nextkin",
        "portal_role": (
            (nextkin.get("portal_role") or "viewer")
            if (nextkin.get("access_type") == "family")
            else None
        ),
        "immediate_access": True,
        "access_timing": nextkin.get("access_timing"),
        "nok_letter_received": nextkin.get("nok_letter_received", False),
        "owner_id": nextkin["owner_id"],
        "owner": owner_summary,
        "didit": {
            **session_public_payload(nextkin),
            **claimant,
            "required": True,
        },
        "death_verification": public_death_verification(owner) if owner else None,
        "vault_push": vault_push_session_payload(owner),
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
    payload: ApproveLivingAccessRequest = Body(
        default_factory=ApproveLivingAccessRequest
    ),
):
    decoded = decode_access_token(request, authorization)
    from app.auth.family_access import DASHBOARD_AREA_SECTION2_NOK
    from app.auth.vault_actor import require_owner_or_family

    _, owner = await require_owner_or_family(
        decoded,
        perm="can_manage_nextkin",
        area_id=DASHBOARD_AREA_SECTION2_NOK,
        detail="Only the owner or a family Admin+ with Section 2 access can approve Next-of-Kin",
    )

    from app.auth.living_release_lock import (
        assert_living_release_unlocked,
        clear_living_release_failures,
        record_living_release_failure,
    )

    proof = payload
    await assert_living_release_unlocked(owner)
    try:
        require_step_up_auth(
            user=owner,
            password=proof.step_up_password(),
            mfa_challenge_token=proof.mfa_challenge_token,
            step_up_token=proof.step_up_token,
        )
    except HTTPException:
        if proof.step_up_password():
            await record_living_release_failure(owner)
        raise
    await clear_living_release_failures(owner)

    nextkin = await users_collection.find_one(
        {
            "_id": ObjectId(nextkin_id),
            "role": "nextkin",
            "owner_id": str(owner["_id"]),
        }
    )
    if not nextkin:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found")

    from app.auth.access_types import is_nextkin_collaborator

    if not is_nextkin_collaborator(nextkin):
        raise HTTPException(
            status_code=400,
            detail="Family collaborators are always active — manage them in Vault Settings",
        )

    nextkin_profile = load_nextkin_profile(dict(nextkin)) or dict(nextkin)
    plain_password = str(nextkin_profile.get("master_password") or "").strip()
    if not plain_password:
        plain_password = generate_temp_password()
        hashed = hash_password(plain_password)
        await users_collection.update_one(
            {"_id": nextkin["_id"]},
            {
                "$set": {
                    "master_password": plain_password,
                    "password_hash": hashed,
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        nextkin_profile["master_password"] = plain_password
        nextkin_profile["password_hash"] = hashed

    await _approve_and_notify_if_needed(
        nextkin_profile,
        owner,
        approved=True,
        plain_password=plain_password,
    )

    return {
        "message": (
            "Access released. They will receive login details now. "
            "If this is not what you intended, revoke their access immediately."
        ),
        "nextkin_email": nextkin["email"],
        "immediate_access": True,
        "immediate_access_pending": False,
    }


@router.post("/approve-all-nextkin-access")
async def approve_all_nextkin_access(
    request: Request,
    authorization: str | None = Header(default=None),
    payload: ApproveLivingAccessRequest = Body(
        default_factory=ApproveLivingAccessRequest
    ),
):
    decoded = decode_access_token(request, authorization)
    from app.auth.family_access import DASHBOARD_AREA_SECTION2_NOK
    from app.auth.vault_actor import require_owner_or_family

    _, owner = await require_owner_or_family(
        decoded,
        perm="can_manage_nextkin",
        area_id=DASHBOARD_AREA_SECTION2_NOK,
        detail="Only the owner or a family Admin+ with Section 2 access can approve Next-of-Kin",
    )

    from app.auth.living_release_lock import (
        assert_living_release_unlocked,
        clear_living_release_failures,
        record_living_release_failure,
    )

    await assert_living_release_unlocked(owner)
    try:
        require_step_up_auth(
            user=owner,
            password=payload.step_up_password(),
            mfa_challenge_token=payload.mfa_challenge_token,
            step_up_token=payload.step_up_token,
        )
    except HTTPException:
        if payload.step_up_password():
            await record_living_release_failure(owner)
        raise
    await clear_living_release_failures(owner)

    from app.auth.access_types import NEXTKIN_ACCESS_MONGO_FILTER, is_nextkin_collaborator

    cursor = users_collection.find(
        {
            "role": "nextkin",
            "owner_id": str(owner["_id"]),
            "immediate_access": False,
            "immediate_access_pending": {"$ne": True},
            "access_revoked": {"$ne": True},
            "$and": [NEXTKIN_ACCESS_MONGO_FILTER],
        }
    )
    approved = 0
    async for nextkin in cursor:
        if not is_nextkin_collaborator(nextkin):
            continue
        nextkin_profile = load_nextkin_profile(dict(nextkin)) or dict(nextkin)
        plain_password = str(nextkin_profile.get("master_password") or "").strip()
        if not plain_password:
            plain_password = generate_temp_password()
            hashed = hash_password(plain_password)
            await users_collection.update_one(
                {"_id": nextkin["_id"]},
                {
                    "$set": {
                        "master_password": plain_password,
                        "password_hash": hashed,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
            nextkin_profile["master_password"] = plain_password
        await _approve_and_notify_if_needed(
            nextkin_profile,
            owner,
            approved=True,
            plain_password=plain_password,
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
    from app.auth.family_access import DASHBOARD_AREA_SECTION2_NOK
    from app.auth.vault_actor import require_owner_or_family

    _, owner = await require_owner_or_family(
        decoded,
        perm="can_manage_nextkin",
        area_id=DASHBOARD_AREA_SECTION2_NOK,
        detail="Only the owner or a family Admin+ with Section 2 access can revoke Next-of-Kin",
    )

    # target nextkin
    nextkin = await users_collection.find_one(
        {"_id": ObjectId(nextkin_id), "role": "nextkin", "owner_id": str(owner["_id"])}
    )
    if not nextkin:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found or not linked to this owner")

    from app.auth.access_types import is_nextkin_collaborator

    if not is_nextkin_collaborator(nextkin):
        raise HTTPException(
            status_code=400,
            detail="Family collaborators are managed in Vault Settings",
        )

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
    from app.auth.family_access import DASHBOARD_AREA_SECTION2_NOK
    from app.auth.vault_actor import require_owner_or_family

    _, owner = await require_owner_or_family(
        decoded,
        perm="can_manage_nextkin",
        area_id=DASHBOARD_AREA_SECTION2_NOK,
        detail="Only the owner or a family Admin+ with Section 2 access can revoke Next-of-Kin",
    )

    from app.auth.access_types import NEXTKIN_ACCESS_MONGO_FILTER, is_nextkin_collaborator

    cursor = users_collection.find({
        "role": "nextkin",
        "owner_id": str(owner["_id"]),
        "$and": [NEXTKIN_ACCESS_MONGO_FILTER],
    })
    nextkins = [nk async for nk in cursor if is_nextkin_collaborator(nk)]

    if not nextkins:
        return {"message": "No Next-of-Kin found for this owner", "updated": 0, "emailed": 0}

    # bulk update in DB first
    now = datetime.utcnow()
    bulk_res = await users_collection.update_many(
        {
            "role": "nextkin",
            "owner_id": str(owner["_id"]),
            "$and": [NEXTKIN_ACCESS_MONGO_FILTER],
        },
        {"$set": {
            "immediate_access": False,
            "immediate_access_pending": False,
            "updated_at": now,
        }, "$unset": {"immediate_access_email_at": ""}},
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
    return await issue_session_for_user(response, updated_user)


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

    authorized = await get_authorized_user_for_email(
        email,
        authorization,
        request=request,
    )
    if not authorized:
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

    authorized = await get_authorized_user_for_email(
        email,
        authorization,
        request=request,
    )
    if not authorized:
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

    from app.auth.collaborator_security import mfa_enrolled_fields

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
                **(
                    mfa_enrolled_fields()
                    if user.get("role") == "nextkin"
                    else {}
                ),
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
        user = await users_collection.find_one(
            {"email": email, "role": {"$in": ["owner", "nextkin"]}}
        )
        if not user:
            raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

        methods = normalize_mfa_methods(user)
        authorized = await get_authorized_user_for_email(
            email, authorization, request=request
        )
        challenge_ok = verify_mfa_challenge_token(
            payload.mfa_challenge_token, email
        )
        # Linked email MFA, authenticated settings, or password-proven login challenge.
        if not methods["email"] and not authorized and not challenge_ok:
            raise HTTPException(
                status_code=403,
                detail="Email MFA is not linked. Sign in and enable it from Vault Settings."
            )

        if not authorized:
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

    user = await users_collection.find_one(
        {"email": email, "role": {"$in": ["owner", "nextkin"]}}
    )

    if not user:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

    methods = normalize_mfa_methods(user)
    authorized = await get_authorized_user_for_email(
        email,
        authorization,
        request=request,
    )
    challenge_ok = verify_mfa_challenge_token(payload.mfa_challenge_token, email)
    if not methods["email"] and not authorized and not challenge_ok:
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

    from app.auth.collaborator_security import (
        collaborator_needs_mfa_enroll,
        mfa_enrolled_fields,
    )

    login_otp_only = (
        user.get("role") == "nextkin"
        and challenge_ok
        and not authorized
        and collaborator_needs_mfa_enroll(user)
    )

    mfa_set = {
        "verified": True,
        "updated_at": datetime.utcnow(),
    }
    if not login_otp_only:
        mfa_set["mfa_enabled"] = True
        mfa_set["primary_mfa"] = user.get("primary_mfa") or "email"
        mfa_set["mfa_methods.email"] = True
        if authorized and user.get("role") == "nextkin":
            mfa_set.update(mfa_enrolled_fields())

    await users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": mfa_set},
    )

    updated_user = await users_collection.find_one({"_id": user["_id"]})
    return await issue_session_for_user(response, updated_user)
# ============================================================
# 🔟 REFRESH TOKEN
# ============================================================
@router.post("/refresh-token")
async def refresh_token(request: Request, response: Response):
    owner_refresh = request.cookies.get("oa_refresh_token")
    nok_refresh = request.cookies.get("oa_nok_refresh_token")
    admin_refresh = request.cookies.get("oa_admin_refresh_token")
    # Family/NOK dashboards send X-OA-Session-Kind so a leftover owner refresh
    # cookie cannot steal the rotation and wipe the collaborator session.
    session_kind = (request.headers.get("X-OA-Session-Kind") or "").strip().lower()
    prefer_nok = session_kind in ("family", "nextkin")

    async def try_role(role: str) -> dict | None:
        try:
            return await refresh_session_from_cookie(
                response,
                request,
                role=role,
            )
        except ValueError:
            return None

    if admin_refresh and not prefer_nok:
        result = await try_role("admin")
        if result is not None:
            return result

    if prefer_nok and nok_refresh:
        result = await try_role("nextkin")
        if result is not None:
            return result

    if owner_refresh and not prefer_nok:
        result = await try_role("owner")
        if result is not None:
            return result

    if nok_refresh:
        result = await try_role("nextkin")
        if result is not None:
            return result

    if owner_refresh:
        result = await try_role("owner")
        if result is not None:
            return result

    if admin_refresh:
        result = await try_role("admin")
        if result is not None:
            return result

    raise HTTPException(status_code=401, detail="Missing refresh session")


@router.get("/session")
async def get_session(request: Request):
    """Return auth state without exposing JWT values to JavaScript."""

    candidates: list[dict] = []

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
        if role == "nextkin":
            from app.auth.access_types import resolve_access_type
            from app.auth.portal_roles import (
                resolve_dashboard_permissions,
                role_label,
            )

            access_type = resolve_access_type(user)
            payload["access_type"] = access_type
            if access_type == "family":
                payload["portal_role"] = user.get("portal_role") or "viewer"
                payload["portal_role_label"] = role_label(user.get("portal_role"))
                payload["dashboard_permissions"] = resolve_dashboard_permissions(
                    user
                )
                payload["authorized_sections"] = user.get(
                    "authorized_sections"
                ) or []
            payload["access_level"] = user.get("access_level")
            payload["full_name"] = user.get("full_name")
            payload["returning_user"] = user_is_returning_for_session(user)

            from app.auth.collaborator_security import collaborator_setup_payload

            payload.update(collaborator_setup_payload(user))

            owner_doc = None
            owner_id = user.get("owner_id")
            if owner_id:
                try:
                    owner_doc = await users_collection.find_one(
                        {"_id": ObjectId(str(owner_id)), "role": "owner"}
                    )
                except Exception:
                    owner_doc = None
            payload["vault_push"] = vault_push_session_payload(owner_doc)
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
            payload["full_name"] = user.get("full_name")
            payload["returning_user"] = user_is_returning_for_session(user)
            payload["vault_push"] = vault_push_session_payload(user)
            payload["notification_prefs"] = get_owner_notification_prefs(user)
            from app.auth.owner_wait import public_owner_wait

            payload["death_claim_alert"] = public_owner_wait(user)
        candidates.append(payload)

    if not candidates:
        return {"authenticated": False}

    # Leftover owner cookies must not hide an active family collaborator session.
    for payload in candidates:
        if (
            payload.get("role") == "nextkin"
            and payload.get("access_type") == "family"
        ):
            return payload
    for payload in candidates:
        if payload.get("role") == "nextkin":
            return payload
    return candidates[0]


class SpecialDayItem(BaseModel):
    kind: str = "custom"
    month: int
    day: int
    label: str | None = None
    enabled: bool = True
    source: str | None = None


class NotificationPreferencesPatch(BaseModel):
    in_app_enabled: bool | None = None
    email_reminders_enabled: bool | None = None
    push_state: str | None = None
    push_for_collaborators: bool | None = None
    section_update_recipient_ids: list[str] | None = None
    section_update_recipients_by_section: dict[str, list[str] | None] | None = None
    special_days_enabled: bool | None = None
    special_days: list[SpecialDayItem] | None = None


@router.get("/notification-preferences")
async def get_notification_preferences(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Owner vault notification policy (device permission is still per-browser)."""
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    owner = await users_collection.find_one(
        {"email": decoded["sub"], "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    prefs = get_owner_notification_prefs(owner)
    return {
        **prefs,
        "vault_push": vault_push_session_payload(owner),
    }


@router.patch("/notification-preferences")
async def patch_notification_preferences(
    body: NotificationPreferencesPatch,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Persist owner vault notification policy for family / NOK prompts."""
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    owner = await users_collection.find_one(
        {"email": decoded["sub"], "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    try:
        next_prefs = merge_notification_prefs_patch(
            owner.get("notification_prefs"),
            body.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await users_collection.update_one(
        {"_id": owner["_id"]},
        {
            "$set": {
                "notification_prefs": next_prefs,
                "updated_at": datetime.utcnow(),
            }
        },
    )
    return {
        **next_prefs,
        "vault_push": vault_push_session_payload(
            {**owner, "notification_prefs": next_prefs}
        ),
    }


class VaultPrivacyBody(BaseModel):
    rules: list[dict] | None = None


@router.get("/vault-privacy")
async def get_vault_privacy(
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    owner = await users_collection.find_one(
        {"email": decoded["sub"], "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    from app.auth.vault_privacy import cache_owner_privacy, get_owner_vault_privacy

    privacy = get_owner_vault_privacy(owner)
    cache_owner_privacy(str(owner["_id"]), privacy)
    return privacy


@router.put("/vault-privacy")
async def put_vault_privacy(
    body: VaultPrivacyBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    owner = await users_collection.find_one(
        {"email": decoded["sub"], "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    from app.auth.vault_privacy import (
        cache_owner_privacy,
        normalize_vault_privacy,
    )

    privacy = normalize_vault_privacy({"rules": body.rules or []})
    await users_collection.update_one(
        {"_id": owner["_id"]},
        {"$set": {"vault_privacy": privacy, "updated_at": datetime.utcnow()}},
    )
    cache_owner_privacy(str(owner["_id"]), privacy)
    return privacy


class VaultZkBody(BaseModel):
    ciphertext: str | None = None


@router.get("/vault-privacy/zk/{section_id}")
async def get_vault_zk_fields(
    section_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    owner = await users_collection.find_one(
        {"email": decoded["sub"], "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    from app.database import vault_zk_fields_collection

    row = await vault_zk_fields_collection.find_one(
        {"owner_id": str(owner["_id"]), "section_id": str(section_id)}
    )
    return {"ciphertext": (row or {}).get("ciphertext") or None}


@router.put("/vault-privacy/zk/{section_id}")
async def put_vault_zk_fields(
    section_id: str,
    body: VaultZkBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")
    owner = await users_collection.find_one(
        {"email": decoded["sub"], "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    from app.database import vault_zk_fields_collection

    cipher = str(body.ciphertext or "").strip()
    if not cipher:
        await vault_zk_fields_collection.delete_one(
            {"owner_id": str(owner["_id"]), "section_id": str(section_id)}
        )
        return {"ok": True, "cleared": True}
    await vault_zk_fields_collection.update_one(
        {"owner_id": str(owner["_id"]), "section_id": str(section_id)},
        {
            "$set": {
                "owner_id": str(owner["_id"]),
                "section_id": str(section_id),
                "ciphertext": cipher,
                "updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )
    return {"ok": True}


class PushSubscriptionBody(BaseModel):
    endpoint: str
    keys: dict
    user_agent: str | None = None


class PushUnsubscribeBody(BaseModel):
    endpoint: str


async def _resolve_session_user(decoded: dict):
    role = decoded.get("role") or "owner"
    if role == "nextkin":
        try:
            user = await users_collection.find_one(
                {"_id": ObjectId(decoded["sub"]), "role": "nextkin"}
            )
        except (InvalidId, KeyError, TypeError):
            user = None
        if not user:
            user = await users_collection.find_one(
                {
                    "email": decoded.get("email") or decoded.get("sub"),
                    "role": "nextkin",
                }
            )
        return user
    return await users_collection.find_one(
        {"email": decoded.get("sub"), "role": "owner"}
    )


@router.get("/vapid-public-key")
async def get_vapid_public_key_route():
    """Public VAPID key for PushManager.subscribe (safe to expose).

    Private key may live only in AWS Secrets / SSM — clients only need the public key.
    """
    from app.notifications.web_push import get_vapid_public_key, vapid_configured

    public = get_vapid_public_key()
    if not public:
        return {
            "configured": False,
            "publicKey": None,
            "message": "Web Push VAPID public key is not configured on the server.",
        }
    ready = vapid_configured()
    return {
        "configured": ready,
        "publicKey": public,
        "message": None
        if ready
        else "VAPID public key is available; ensure VAPID_PRIVATE_KEY is loaded from secrets for delivery.",
    }


@router.post("/push-subscribe")
async def push_subscribe(
    body: PushSubscriptionBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Store a browser PushSubscription for the signed-in owner / family / NOK."""
    from app.notifications.web_push import upsert_push_subscription, vapid_configured

    if not vapid_configured():
        raise HTTPException(
            status_code=503,
            detail="Web Push is not configured (missing VAPID keys).",
        )

    decoded = decode_owner_or_nok_token(request, authorization)
    user = await _resolve_session_user(decoded)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        await upsert_push_subscription(
            user["_id"],
            {
                "endpoint": body.endpoint,
                "keys": body.keys,
                "user_agent": body.user_agent,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"ok": True, "message": "Push subscription saved"}


@router.post("/push-unsubscribe")
async def push_unsubscribe(
    body: PushUnsubscribeBody,
    request: Request,
    authorization: str | None = Header(default=None),
):
    from app.notifications.web_push import remove_push_subscription

    decoded = decode_owner_or_nok_token(request, authorization)
    user = await _resolve_session_user(decoded)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    await remove_push_subscription(user["_id"], body.endpoint)
    return {"ok": True}


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

    from app.auth.collaborator_security import collaborator_setup_payload

    payload = {
        "email": user["email"],
        "phone": user.get("phone"),
        "role": user.get("role", "owner"),
        "mfa_enabled": any(normalize_mfa_methods(user).values()),
        "primary_mfa": user.get("primary_mfa"),
        "mfa_methods": normalize_mfa_methods(user),
    }
    if user.get("role") == "nextkin":
        payload.update(collaborator_setup_payload(user))
    if user.get("role") == "owner":
        from app.legal.death_certificate_authorization import agreement_status

        payload["death_certificate_authorization"] = agreement_status(user)
        from app.auth.owner_wait import public_owner_wait

        payload["death_claim_alert"] = public_owner_wait(user)
    return payload


@router.post("/collaborator-change-password")
async def collaborator_change_password(
    payload: CollaboratorPasswordChangeRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """First-login password reset for family collaborators and next of kin."""
    decoded = decode_owner_or_nok_token(request, authorization)
    if decoded.get("role") != "nextkin":
        raise HTTPException(status_code=403, detail="Only vault collaborators can use this")

    user = await _resolve_session_user(decoded)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    current = (payload.current_password or "").strip()
    new_password = (payload.new_password or "").strip()
    stored = user.get("password_hash") or user.get("password") or ""
    if not current or not verify_password(current, stored):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    if current == new_password:
        raise HTTPException(
            status_code=400,
            detail="Choose a new password that is different from the one you were given",
        )

    from app.auth.collaborator_security import (
        collaborator_setup_payload,
        password_changed_fields,
    )

    await users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_hash": hash_password(new_password),
                "master_password": new_password,
                "updated_at": datetime.utcnow(),
                **password_changed_fields(),
            }
        },
    )
    updated = await users_collection.find_one({"_id": user["_id"]})
    return {
        "message": "Password updated",
        **collaborator_setup_payload(updated),
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

    role = decoded.get("role") or "owner"
    if role == "nextkin":
        try:
            user = await users_collection.find_one(
                {"_id": ObjectId(decoded["sub"]), "role": "nextkin"}
            )
        except Exception:
            user = None
        if not user:
            user = await users_collection.find_one(
                {
                    "email": decoded.get("email") or decoded["sub"],
                    "role": "nextkin",
                }
            )
    else:
        user = await users_collection.find_one(
            {"email": decoded["sub"], "role": "owner"}
        )
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

    try:
        summary = await purge_owner_account(
            owner,
            deleted_by="self",
            reason="owner_self_delete",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

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

    from app.security.vault_principals import require_nok_principal

    require_nok_principal(nextkin, detail=NOK_LOGIN_GENERIC)

    from app.auth.access_types import is_family_collaborator

    if is_family_collaborator(nextkin):
        raise HTTPException(
            status_code=403,
            detail="Family collaborators cannot report a passing.",
        )

    if not verify_password(payload.master_password, nextkin.get("password_hash", "")):
        raise HTTPException(status_code=401, detail=NOK_LOGIN_GENERIC)

    if not nextkin.get("immediate_access", False) or nextkin.get("access_revoked"):
        raise HTTPException(status_code=403, detail=NOK_LOGIN_GENERIC)

    from app.auth.didit import DIDIT_APPROVED, claims_require_didit

    if claims_require_didit() and nextkin.get("didit_status") != DIDIT_APPROVED:
        from app.auth.after_death_policy import didit_needs_manual_review

        if didit_needs_manual_review(nextkin.get("didit_status")):
            await users_collection.update_one(
                {"_id": nextkin["_id"]},
                {
                    "$set": {
                        "didit_manual_review_required": True,
                        "didit_manual_review_reason": (
                            "Identity was not Approved."
                        ),
                    }
                },
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    "Your identity check was not Approved. It is in manual review. "
                    "You cannot report a passing until identity is Approved."
                ),
            )
        raise HTTPException(
            status_code=400,
            detail="Verify your identity (ID and selfie) before reporting a passing.",
        )

    try:
        owner = await users_collection.find_one(
            {"_id": ObjectId(nextkin["owner_id"]), "role": "owner"}
        )
    except Exception:
        owner = None

    if not owner:
        raise HTTPException(status_code=400, detail=NOK_LOGIN_GENERIC)

    if owner.get("owner_status") == "deceased":
        return {
            "status": "deceased",
            "already_reported": True,
            "pending_review": False,
            "message": "This passing has already been recorded.",
            "upon_death_granted": 0,
        }

    from app.security.auth_rate_limit import enforce_auth_rate_limit

    await enforce_auth_rate_limit(
        request,
        key=f"nok-death-report:{nextkin['_id']}",
    )

    result = await record_pending_death_report(
        owner=owner,
        reported_by_nextkin=nextkin,
        source="nok_manual_report",
    )

    if result.get("status") == "deceased":
        return {
            "status": "deceased",
            "already_reported": True,
            "pending_review": False,
            "message": "This passing has already been recorded.",
            "upon_death_granted": 0,
        }

    already = bool(result.get("already_reported"))
    return {
        "status": "pending_review",
        "already_reported": already,
        "pending_review": True,
        "message": (
            "We already have this passing report. Orderly Affairs is verifying "
            "it. Vault access stays sealed until our team releases it."
            if already
            else (
                "Passing report received. Upload the death certificate next. "
                "The owner is notified when that file is stored, independent "
                "death records are checked, and a 7-day hold starts. Vault "
                "access stays sealed until our team releases it."
            )
        ),
        "upon_death_granted": 0,
    }


class StopAfterDeathRequest(BaseModel):
    password: str


@router.post("/stop-after-death-request")
async def stop_after_death_request(
    payload: StopAfterDeathRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only the vault owner can stop this request")
    owner = await users_collection.find_one(
        {"email": str(decoded.get("sub") or "").lower(), "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    stored = owner.get("password_hash") or owner.get("password") or ""
    if not payload.password or not verify_password(payload.password, stored):
        raise HTTPException(status_code=401, detail="Password is incorrect")

    from app.auth.after_death_case import dispute_case, open_case_for_owner

    case = await open_case_for_owner(str(owner["_id"]))
    if not case:
        return {"ok": True, "already_stopped": True}
    ip = request.client.host if request.client else None
    await dispute_case(case=case, owner=owner, method="i_am_alive", ip=ip)
    return {"ok": True, "status": "OWNER_DISPUTED"}


@router.put("/owner/status")
async def update_owner_status(
    status: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)

    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only the kit owner may update status")

    if status not in ["alive", "deceased"]:
        raise HTTPException(400, "Invalid status")

    if status == "deceased":
        raise HTTPException(
            status_code=403,
            detail=(
                "Owner status cannot be set to deceased here. A verified "
                "Next-of-Kin reports a passing, then Orderly Affairs releases "
                "vault access from the admin portal."
            ),
        )

    result = await users_collection.update_one(
        {"email": decoded["sub"], "role": "owner"},
        {"$set": {"owner_status": status}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Owner not found")

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

    user = await users_collection.find_one(
        {"email": email, "role": "owner"}
    )
    if not user:
        user = await users_collection.find_one(
            {"email": email, "role": "nextkin"}
        )

    if not user:
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

    from app.auth.collaborator_security import password_reset_identity

    return {
        "message": PASSWORD_RESET_GENERIC_MESSAGE,
        **password_reset_identity(user),
    }

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

    user = await users_collection.find_one(
        {"email": email, "role": "owner"}
    )
    if not user:
        user = await users_collection.find_one(
            {"email": email, "role": "nextkin"}
        )

    if not user:
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

    if user and user.get("role") == "nextkin":
        from app.auth.collaborator_security import password_changed_fields

        await users_collection.update_one(
            {"_id": user["_id"]},
            {
                "$set": {
                    "password_hash": hashed_password,
                    "master_password": payload.new_password,
                    "updated_at": datetime.utcnow(),
                    **password_changed_fields(),
                }
            },
        )
    else:
        await users_collection.update_one(
            {"email": email, "role": "owner"},
            {
                "$set": {
                    "password": hashed_password,
                    "updated_at": datetime.utcnow(),
                    # Force new E2EE envelope on next login (old wrap is password-bound).
                    # Pre-existing v3 ciphertext needs a backup restore if the DEK was lost.
                    "e2ee": {
                        "password_reset_at": datetime.utcnow(),
                        "needs_setup": True,
                        "version": 1,
                    },
                }
            },
        )

    # 🔒 Delete used OTP
    await otp_collection.delete_many({
        "email": email,
        "type": "password_reset"
    })

    log_device_fingerprint(request, "password_change", subject=email)

    await reset_auth_rate_limit(request, key=f"reset-password:{email}")

    if user and user.get("role") == "nextkin":
        return {"message": "Password reset successful"}

    return {
        "message": "Password reset successful",
        "e2ee_needs_setup": True,
        "e2ee_note": (
            "Vault encryption keys must be re-created on next sign-in. "
            "Sections already saved with E2EE (v3) require a backup restore "
            "if you no longer have the previous decryption key."
        ),
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

    user = await users_collection.find_one(
        {"email": email, "role": {"$in": ["owner", "nextkin"]}}
    )
    if not user:
        raise HTTPException(status_code=400, detail=MFA_GENERIC_ERROR)

    methods = normalize_mfa_methods(user)
    authorized = await get_authorized_user_for_email(
        email,
        authorization,
        request=request,
    )
    skip_captcha = verify_mfa_challenge_token(payload.mfa_challenge_token, email)
    if not methods["email"] and not authorized and not skip_captcha:
        raise HTTPException(
            status_code=403,
            detail="Email MFA is not linked. Sign in and enable it from Vault Settings."
        )

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
