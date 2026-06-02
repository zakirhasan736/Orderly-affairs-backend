from fastapi import APIRouter, Request, HTTPException, Header, Depends 
from typing import List, Union

from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
from random import randint
from app.auth.service import trigger_death_letters
from bson.errors import InvalidId
from secrets import token_urlsafe
from io import BytesIO
import pyotp, qrcode, base64, random, string, sendgrid
from sendgrid.helpers.mail import Mail
from bson import ObjectId
from passlib.context import CryptContext
from app.security.usage_guard import enforce_usage
from app.auth.phone import format_phone
from app.auth.twilio_verify import (
    send_verification_code,
    check_verification_code,
)

from app.database import users_collection, otp_collection, sms_mfa_attempts_collection, pending_signup_collection
from app.security.billing_guard import enforce_billing
from app.security.password_handler import hash_password, verify_password
from app.security.jwt_handler import create_access_token, verify_token
from app.config import settings
from datetime import datetime
from app.notifications.nextkin_emails import (
    send_nextkin_email,
    NextKinEmailEvent,
)
import string, random

from sendgrid import SendGridAPIClient

router = APIRouter(prefix="/auth", tags=["auth"])
# pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# ============================================================
# MODELS
# ============================================================
class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str | None = None
    phone_number: str | None = None
    mfa_method: str | None = None  # "sms" | "email" | "authenticator"

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class VerifyTOTPRequest(BaseModel):
    email: EmailStr
    code: str

class EmailRequest(BaseModel):
    email: EmailStr

class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: int

class LinkAuthenticatorRequest(BaseModel):
    email: EmailStr
    code: str
    secret: str

class OwnerResetRequest(BaseModel):
    email: EmailStr

class OwnerResetPassword(BaseModel):
    email: EmailStr
    otp: int
    new_password: str


class StartSMSMFARequest(BaseModel):
    email: EmailStr
    phoneNumber: str | None = None


class VerifySMSOTPRequest(BaseModel):
    email: EmailStr
    code: str


class ResendSignupSMSRequest(BaseModel):
    email: EmailStr

class PhoneRequest(BaseModel):
    phoneNumber: str

class MFAMethodRequest(BaseModel):
    method: str

# ---- Next-of-Kin ----
class NextKinCreateRequest(BaseModel):
    email: EmailStr
    full_name: str
    relationship: str
    phone_number: str | None = None
    access_level: str = "full" 
    authorized_sections: list[str] | None = []
    immediate_access: bool | None = False
    nok_letter_received: bool | None = False
    master_password: str | None = None
    password_card_generated: bool | None = False
    card_storage_location: str | None = None
    special_instructions: str | None = None

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
    methods = normalize_mfa_methods(user)
    preferred = user.get("primary_mfa")
    if preferred not in methods or not methods.get(preferred):
        preferred = first_enabled_mfa_method(methods)

    return {
        "message": "Password verified",
        "mfa_required": True,
        "method": preferred,
        "methods": [method for method, enabled in methods.items() if enabled],
        "mfa_methods": methods,
        "email": user["email"],
        "phone": user.get("phone"),
        "billing_status": billing.get("status", "pending"),
        "requires_billing": billing.get("status") in ["pending", "blocked"],
    }


async def get_authorized_owner_for_email(
    email: str,
    authorization: str | None,
) -> dict | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None

    decoded = verify_token(authorization.split(" ")[1])
    if not decoded:
        return None

    owner = await users_collection.find_one({
        "email": decoded["sub"],
        "role": "owner"
    })
    if not owner or owner.get("email") != email:
        return None

    return owner

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
    return {
        "email": email,
        "password": hashed_password,
        "full_name": full_name,
        "phone": phone,
        "role": "owner",
        "owner_id": None,
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
            "payment_method_attached": False,
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
    new_user = build_owner_user_document(
        email=pending["email"],
        hashed_password=pending["password"],
        full_name=pending.get("full_name"),
        phone=pending.get("phone"),
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
async def enforce_sms_resend_cooldown(phone: str, cooldown_seconds: int = 30):
    recent = await sms_mfa_attempts_collection.find_one(
        {"phone": phone, "type": "sms_verify_send"},
        sort=[("created_at", -1)]
    )

    if recent:
        delta = datetime.utcnow() - recent["created_at"]
        if delta.total_seconds() < cooldown_seconds:
            remaining = int(cooldown_seconds - delta.total_seconds())
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {remaining}s before requesting another OTP"
            )

    await sms_mfa_attempts_collection.insert_one({
        "phone": phone,
        "type": "sms_verify_send",
        "created_at": datetime.utcnow()
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
async def _approve_and_notify_if_needed(nextkin: dict, owner: dict, approved: bool = True):
    if bool(nextkin.get("immediate_access", False)) == approved:
        return

    await users_collection.update_one(
        {"_id": nextkin["_id"]},
        {
            "$set": {
                "immediate_access": approved,
                **({"nok_letter_received": False} if approved else {}),
                "updated_at": datetime.utcnow(),
            }
        },
    )

    try:
        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)

        login_url = f"{settings.FRONTEND_URL}/nextkin-login"

        message = Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=nextkin["email"],
            subject="Orderly Affairs – Immediate Access Granted",
            html_content=f"""
            <div style="font-family: Arial, sans-serif; line-height:1.6; color:#333;">
            
            <p>Hello {nextkin.get("full_name")},</p>

            <p>
                <b>{owner.get("full_name") or owner["email"]}</b> has granted you 
                <b>Immediate Access</b> to their <b>Orderly Affairs Kit</b>.
            </p>

            <p>
                You may now log in and view the sections that have been made available to you.
            </p>

            <p><b>Login Details:</b></p>

            <ul>
                <li>Email: {nextkin["email"]}</li>
                {f"<li>Password: {plain_password}</li>" if plain_password else ""}
            </ul>

            <p>
                <a href="{login_url}" 
                style="
                    display:inline-block;
                    padding:10px 18px;
                    background:#2563eb;
                    color:#ffffff;
                    text-decoration:none;
                    border-radius:6px;
                    font-weight:bold;">
                Log in to Orderly Affairs
                </a>
            </p>

            <p>
                For security reasons, we recommend logging in and updating your password after your first access.
            </p>

            <hr style="margin-top:30px;margin-bottom:20px"/>

            <small style="color:#666;">
                Access granted on {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}
            </small>

            </div>
            """,
        )
        sg.send(message)
    except Exception as e:
        print("⚠️ Immediate-access email failed:", e)

async def notify_owner_nextkin_login(*, owner: dict, nextkin: dict):
    try:
        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)

        subject = "Orderly Affairs – Next-of-Kin Access Alert"

        html = f"""
        <div style="font-family:Arial,sans-serif;line-height:1.6">
          <h3>Next-of-Kin Login Alert</h3>
          <p>
            <b>{nextkin.get("full_name") or nextkin["email"]}</b>
            has just accessed your Orderly Affairs Kit.
          </p>
          <ul>
            <li><b>Access Level:</b> {nextkin.get("access_level")}</li>
            <li><b>Email:</b> {nextkin["email"]}</li>
            <li><b>Time:</b> {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}</li>
          </ul>
          <p>
            If this access was unexpected, you can revoke access immediately
            from your Owner Dashboard.
          </p>
          <hr />
          <small>Orderly Affairs Security Notification</small>
        </div>
        """

        message = Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=owner["email"],
            subject=subject,
            html_content=html,
        )

        sg.send(message)

    except Exception as e:
        # ⚠️ Never block login
        print("⚠️ Owner login notification failed:", e)

# ============================================================
# 1️⃣ OWNER SIGNUP
# ============================================================

@router.post("/signup")
async def signup(user: SignupRequest):
    email = user.email.lower().strip()

    # real user already exists
    existing_user = await users_collection.find_one({"email": email})
    if existing_user:
        raise HTTPException(status_code=400, detail="User already exists")

    # remove expired pending signups first
    await delete_expired_pending_signup(email)

    # pending signup already exists
    existing_pending = await get_active_pending_signup(email)
    if existing_pending:
        raise HTTPException(
            status_code=400,
            detail="Signup already started. Please complete verification or use resend."
        )

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

    hashed_pw = hash_password(user.password)

    pending_doc = {
        "email": email,
        "password": hashed_pw,
        "full_name": user.full_name,
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
            await enforce_sms_resend_cooldown(phone)
            send_verification_code(phone)
        except Exception as e:
            await pending_signup_collection.delete_one({"email": email})
            raise HTTPException(status_code=400, detail=str(e))

        return {
            "message": "Signup started. OTP sent to phone.",
            "otp_required": True,
            "method": "sms",
            "email": email,
            "phone": phone,
            "flow": "signup"
        }

    # Email signup
    if user.mfa_method == "email":
        otp = randint(100000, 999999)
        expiry = datetime.utcnow() + timedelta(minutes=10)

        pending_doc["email_otp"] = otp
        pending_doc["email_otp_expires"] = expiry

        await pending_signup_collection.insert_one(pending_doc)

        try:
            sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
            message = Mail(
                from_email=settings.EMAIL_SENDER,
                to_emails=email,
                subject="Your Orderly Affairs verification code",
                html_content=f"<p>Your verification code is <b>{otp}</b>. It expires in 10 minutes.</p>",
            )
            sg.send(message)
        except Exception as e:
            await pending_signup_collection.delete_one({"email": email})
            raise HTTPException(status_code=400, detail=f"Failed to send email OTP: {str(e)}")

        return {
            "message": "Signup started. OTP sent to email.",
            "otp_required": True,
            "method": "email",
            "email": email,
            "flow": "signup"
        }

    # Authenticator signup
    if user.mfa_method == "authenticator":
        secret = pyotp.random_base32()
        pending_doc["provisioned_secret"] = secret

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
            "secret": secret,
            "flow": "signup"
        }

    # no MFA
    new_user = build_owner_user_document(
        email=email,
        hashed_password=hashed_pw,
        full_name=user.full_name,
        phone=phone,
        mfa_method=None,
    )

    await users_collection.insert_one(new_user)

    return {
        "message": "Owner account created successfully.",
        "otp_required": False,
    }

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
async def owner_login(data: LoginRequest):
    email = data.email.lower().strip()

    # do not let pending signup pretend to be a real user
    pending = await pending_signup_collection.find_one({
        "email": email,
        "expires_at": {"$gt": datetime.utcnow()}
    })
    if pending:
        raise HTTPException(
            status_code=403,
            detail="Signup not completed yet. Please finish MFA verification first."
        )

    user = await users_collection.find_one({
        "email": email,
        "role": "owner"
    })

    if not user:
        raise HTTPException(status_code=404, detail="Owner not found")

    if not verify_password(data.password, user["password"]):
        raise HTTPException(status_code=400, detail="Invalid credentials")

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
            return mfa_login_response(user, billing)

    token = create_access_token(user)

    return {
        "message": "Login successful",
        "access_token": token,
        "email": email,
        "role": "owner",
        "mfa_required": False,
        "billing_status": billing.get("status", "pending"),
        "requires_billing": billing.get("status") in ["pending", "blocked"]
    }

# ============================================================
# 3️⃣ NEXT-OF-KIN LOGIN
# ============================================================
@router.post("/nextkin-login")
async def nextkin_login(request: Request):
    data = await request.json()
    email = data.get("email", "").lower().strip()
    master_password = data.get("master_password")

    if not email or not master_password:
        raise HTTPException(status_code=400, detail="Email and master_password required")

    user = await users_collection.find_one({"email": email, "role": "nextkin"})
    if not user:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found")

    if not verify_password(master_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid password")
    
    if not user.get("immediate_access", False):
        raise HTTPException(
            status_code=403,
            detail="Access not approved by the Kit Owner"
        )
    
    token = create_access_token(
        user_data=user,   # ✅ FULL DOCUMENT
        expires_delta=timedelta(days=7),
    )
    
    owner = await users_collection.find_one(
        {"_id": ObjectId(user["owner_id"]), "role": "owner"}
    )

    if owner and owner.get("billing", {}).get("status") == "blocked":
        raise HTTPException(
            status_code=403,
            detail="Owner account is inactive due to billing"
        )

    if owner:
        await notify_owner_nextkin_login(owner=owner, nextkin=user)

    return {
        "access_token": token,
        "role": "nextkin",
        "owner_id": str(user["owner_id"]),
        "message": "Next-of-Kin login successful",
    }

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
    authorization: str = Header(None)
):
    """Create one or many Next-of-Kin users. Same endpoint handles single or list payloads."""

    # 1️⃣ Auth check
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ")[1]
    decoded = verify_token(token)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

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
        email = req.email.lower()

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
            "full_name": req.full_name,
            "relationship": req.relationship,
            "phone_number": req.phone_number,

            "access_level": req.access_level,
            "authorized_sections": req.authorized_sections or [],
            "immediate_access": False,
            "nok_letter_received": (
                bool(req.nok_letter_received) if not req.immediate_access else False
            ),

            "password_card_generated": req.password_card_generated,
            "card_storage_location": req.card_storage_location,
            "special_instructions": req.special_instructions,

            "password_hash": hash_password(plain_password),

            "role": "nextkin",
            "owner_id": str(owner["_id"]),
            "verified": True,
            "mfa_enabled": False,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        insert_res = await users_collection.insert_one(new_nok)
        new_id = insert_res.inserted_id

        # 🔥 IF owner checked "Immediate Access" at creation time
        nextkin = await users_collection.find_one({"_id": new_id})
        # await _approve_and_notify_if_needed(nextkin, owner)
        if req.immediate_access:
             await _approve_and_notify_if_needed(nextkin, owner, approved=True)

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

        # ✅ CASE 2: No immediate access → send CREATED email ONLY
        else:
            await send_nextkin_email(
                event=NextKinEmailEvent.CREATED,
                nextkin=nextkin,
                owner=owner,
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
    }



# ============================================================
# 5️⃣ GET ALL NEXT-OF-KIN FOR LOGGED-IN OWNER
# ============================================================
@router.get("/my-nextkin")
async def get_my_nextkin(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ")[1]
    decoded = verify_token(token)
    if not decoded or decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can view next-kin")
    owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    nextkins = users_collection.find({"owner_id": str(owner["_id"]), "role": "nextkin"})
    results = []
    async for nk in nextkins:
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
            "card_storage_location": nk.get("card_storage_location"),
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
    authorization: str = Header(None)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ")[1]
    decoded = verify_token(token)
    if not decoded or decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can update Next-of-Kin")

    owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    nextkin = await users_collection.find_one(
        {"_id": ObjectId(nextkin_id), "role": "nextkin", "owner_id": str(owner["_id"])}
    )
    if not nextkin:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found or not linked to this owner")

    # ✅ Only update provided fields
    update_data = {k: v for k, v in payload.dict().items() if v is not None}

    if update_data.get("immediate_access") is True:
        update_data["nok_letter_received"] = False

    # Optional: hash master_password if changed
    if payload.master_password:
        update_data["password_hash"] = hash_password(payload.master_password)

    if not update_data:
        raise HTTPException(status_code=400, detail="No valid fields provided to update")

    await users_collection.update_one({"_id": ObjectId(nextkin_id)}, {"$set": update_data})

    return {
        "message": f"Next-of-Kin updated successfully.",
        "nextkin_id": nextkin_id,
        "updated_fields": list(update_data.keys()),
    }

# ============================================================
# 14️⃣ DELETE NEXT-OF-KIN (Owner only)
# ============================================================
@router.delete("/delete-nextkin/{nextkin_id}")
async def delete_nextkin(nextkin_id: str, authorization: str = Header(None)):
    """
    Allows an owner to delete a Next-of-Kin they created.
    """
    # 1️⃣ Auth check
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ")[1]
    decoded = verify_token(token)
    if not decoded or decoded.get("role") != "owner":
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
        sg = sendgrid.SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        message = Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=nextkin["email"],
            subject="Orderly Affairs - Next-of-Kin Account Deleted",
            html_content=f"""
            <div style='font-family:Arial,sans-serif'>
              <p>Hello {nextkin.get("full_name") or nextkin["email"]},</p>
              <p>Your Next-of-Kin account under <b>{owner.get("full_name") or owner["email"]}</b> has been deleted.</p>
              <p>If you believe this was a mistake, please contact the account owner directly.</p>
              <hr/>
              <small>Deleted on {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</small>
            </div>
            """,
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
async def get_nextkin_access(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    if not decoded or decoded.get("role") != "nextkin":
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

    return {
        "full_access": full_access,
        "authorized_sections": "all" if full_access else nextkin.get("authorized_sections", []),
        "access_level": access_level,
        "immediate_access": True,
        "nok_letter_received": nextkin.get("nok_letter_received", False),
        "owner_id": nextkin["owner_id"],
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
    authorization: str = Header(None),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ")[1]
    decoded = verify_token(token)
    if not decoded or decoded.get("role") != "owner":
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

    await _approve_and_notify_if_needed(nextkin, owner, approved=True)

    return {
        "message": "Next-of-Kin access approved",
        "nextkin_email": nextkin["email"],
        "immediate_access": True,
    }

# ============================================================
# REVOKE a single Next-of-Kin's access
# ============================================================
@router.post("/revoke-nextkin-access/{nextkin_id}")
async def revoke_nextkin_access(
    nextkin_id: str,
    authorization: str = Header(None),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ")[1]
    decoded = verify_token(token)
    if not decoded or decoded.get("role") != "owner":
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
    authorization: str = Header(None),
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ")[1]
    decoded = verify_token(token)
    if not decoded or decoded.get("role") != "owner":
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
async def verify_totp(payload: VerifyTOTPRequest):
    user = await users_collection.find_one({"email": payload.email.lower()})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.get("totp_secret"):
        raise HTTPException(status_code=400, detail="Authenticator not set up")
    totp = pyotp.TOTP(user["totp_secret"])
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

    updated_user = await users_collection.find_one({"email": payload.email.lower()})
    token = create_access_token(updated_user)
    return {"access_token": token, "message": "Login successful"}


# ============================================================
# 7️⃣ GENERATE MFA QR
# ============================================================
@router.post("/generate-mfa")
async def generate_mfa(payload: EmailRequest, authorization: str | None = Header(default=None)):
    email = payload.email.lower().strip()
    user = await users_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    methods = normalize_mfa_methods(user)
    if user.get("mfa_linked") and methods["authenticator"]:
        raise HTTPException(status_code=400, detail="Authenticator already linked")

    authorized_owner = await get_authorized_owner_for_email(email, authorization)
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
    await users_collection.update_one({"email": email}, {"$set": {"provisioned_secret": secret}})
    return {"qrCodeUrl": f"data:image/png;base64,{img_base64}", "secret": secret}


# ============================================================
# 8️⃣ LINK AUTHENTICATOR
# ============================================================
@router.post("/link-authenticator")
async def link_authenticator(
    payload: LinkAuthenticatorRequest,
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
        secret = pending.get("provisioned_secret")
        if not secret:
            raise HTTPException(status_code=400, detail="Authenticator setup not started")

        totp = pyotp.TOTP(secret)
        if not totp.verify(payload.code):
            raise HTTPException(status_code=400, detail="Invalid verification code")

        pending["totp_secret"] = secret
        created_user = await create_real_user_from_pending(pending)
        token = create_access_token(created_user)

        return {
            "access_token": token,
            "message": "Authenticator signup completed successfully"
        }

    # existing real user flow
    user = await users_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    authorized_owner = await get_authorized_owner_for_email(email, authorization)
    if not authorized_owner:
        raise HTTPException(
            status_code=403,
            detail="Sign in and enable authenticator MFA from Vault Settings."
        )

    totp = pyotp.TOTP(payload.secret)
    if not totp.verify(payload.code):
        raise HTTPException(status_code=400, detail="Invalid verification code")

    await users_collection.update_one(
        {"email": email},
        {
            "$set": {
                "totp_secret": payload.secret,
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
    token = create_access_token(updated_user)
    return {"access_token": token, "message": "Authenticator linked successfully"}
# ============================================================
# 9️⃣ EMAIL OTP — SEND & VERIFY
# ============================================================
@router.post("/send-email")
async def send_email_otp(
    payload: EmailRequest,
    authorization: str | None = Header(default=None),
):
    email = payload.email.lower().strip()

    otp = randint(100000, 999999)
    expiry = datetime.utcnow() + timedelta(minutes=10)

    # ✅ 1. Pending signup email MFA
    pending = await pending_signup_collection.find_one({
        "email": email,
        "mfa_method": "email",
        "expires_at": {"$gt": datetime.utcnow()}
    })

    if pending:
        await pending_signup_collection.update_one(
            {"_id": pending["_id"]},
            {
                "$set": {
                    "email_otp": otp,
                    "email_otp_expires": expiry,
                    "updated_at": datetime.utcnow()
                }
            }
        )
    else:
        # ✅ 2. Existing login email MFA
        user = await users_collection.find_one({"email": email, "role": "owner"})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        methods = normalize_mfa_methods(user)
        authorized_owner = await get_authorized_owner_for_email(email, authorization)
        if not methods["email"] and not authorized_owner:
            raise HTTPException(
                status_code=403,
                detail="Email MFA is not linked. Sign in and enable it from Vault Settings."
            )

        await otp_collection.delete_many({"email": email})
        await otp_collection.insert_one({
            "email": email,
            "otp": otp,
            "expires": expiry,
            "created_at": datetime.utcnow()
        })

    try:
        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        message = Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=email,
            subject="Your Orderly Affairs verification code",
            html_content=f"""
            <p>Your verification code is <b>{otp}</b>.</p>
            <p>It expires in 10 minutes.</p>
            """,
        )
        response = sg.send(message)

        print("✅ Email OTP sent:", response.status_code)

    except Exception as e:
        print("❌ SendGrid Email Error:", str(e))
        raise HTTPException(
            status_code=400,
            detail=f"Failed to send verification email: {str(e)}"
        )

    return {
        "message": f"Verification code sent to {email}"
    }


@router.post("/verify-email")
async def verify_email_code(
    payload: VerifyEmailRequest,
    authorization: str | None = Header(default=None),
):
    email = payload.email.lower().strip()

    # first check pending signup email flow
    pending = await pending_signup_collection.find_one({
        "email": email,
        "mfa_method": "email",
        "expires_at": {"$gt": datetime.utcnow()}
    })

    if pending:
        otp = pending.get("email_otp")
        otp_expires = pending.get("email_otp_expires")

        if not otp or not otp_expires:
            raise HTTPException(status_code=400, detail="No signup OTP found")

        if datetime.utcnow() > otp_expires:
            raise HTTPException(status_code=400, detail="OTP expired")

        if otp != payload.code:
            raise HTTPException(status_code=400, detail="Invalid OTP")

        created_user = await create_real_user_from_pending(pending)
        token = create_access_token(created_user)

        return {
            "access_token": token,
            "message": "Signup email verification successful"
        }

    # otherwise normal login email MFA
    record = await otp_collection.find_one({"email": email})
    if not record:
        raise HTTPException(status_code=400, detail="No OTP found")

    if datetime.utcnow() > record["expires"]:
        raise HTTPException(status_code=400, detail="OTP expired")

    if record["otp"] != payload.code:
        raise HTTPException(status_code=400, detail="Invalid OTP")

    user = await users_collection.find_one({
        "email": email,
        "role": "owner"
    })

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    methods = normalize_mfa_methods(user)
    authorized_owner = await get_authorized_owner_for_email(email, authorization)
    if not methods["email"] and not authorized_owner:
        raise HTTPException(
            status_code=403,
            detail="Email MFA is not linked. Sign in and enable it from Vault Settings."
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
    token = create_access_token(updated_user)
    return {
        "access_token": token,
        "message": "Login successful via email MFA",
    }
# ============================================================
# 🔟 REFRESH TOKEN
# ============================================================
@router.post("/refresh-token")
async def refresh_token(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ")[1]
    decoded = verify_token(token)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if decoded.get("role") == "nextkin":
     user = await users_collection.find_one(
            {"_id": ObjectId(decoded["sub"]), "role": "nextkin"}
     )
    else:
        user = await users_collection.find_one(
            {"email": decoded["sub"], "role": "owner"}
        )

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    new_token = create_access_token(user)
    return {"access_token": new_token, "message": "Token refreshed"}


# ============================================================
# 11️⃣ /me (Protected)
# ============================================================
@router.get("/me")
async def get_me(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.split(" ")[1]
    decoded = verify_token(token)
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = await users_collection.find_one({"email": decoded["sub"]})

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
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
async def disable_mfa_method(payload: MFAMethodRequest, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")

    method = payload.method
    if method not in MFA_METHODS:
        raise HTTPException(status_code=400, detail="Invalid MFA method")

    decoded = verify_token(authorization.split(" ")[1])
    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = await users_collection.find_one({
        "email": decoded["sub"],
        "role": "owner"
    })
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

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
async def reset_mfa(authorization: str = Header(None)):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    user = await users_collection.find_one({"email": decoded["sub"]})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

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
async def owner_logout(authorization: str | None = Header(default=None)):
    if authorization:
        token = authorization.split(" ")[1]
        decoded = verify_token(token) or {}
        if decoded.get("role") and decoded["role"] != "owner":
            raise HTTPException(status_code=403, detail="Not an owner token")
    return {"message": "Owner logged out"}

@router.post("/nextkin-logout")
async def nextkin_logout(authorization: str | None = Header(default=None)):
    if authorization:
        token = authorization.split(" ")[1]
        decoded = verify_token(token) or {}
        if decoded.get("role") and decoded["role"] != "nextkin":
            raise HTTPException(status_code=403, detail="Not a next-of-kin token")
    return {"message": "Next-of-Kin logged out"}

@router.put("/owner/status")
async def update_owner_status(
    status: str,
    authorization: str = Header(...)
):
    user = verify_token(authorization.split(" ")[1])

    if status not in ["alive", "deceased"]:
        raise HTTPException(400, "Invalid status")

    # await users_collection.update_one(
    #     {"_id": user["sub"]},
    #     {"$set": {"owner_status": status}}
    # )
    await users_collection.update_one(
    {"email": user["sub"], "role": "owner"},
    {"$set": {"owner_status": status}}
)

    # 🔥 TRIGGER DEATH LETTERS
    if status == "deceased":
        await trigger_death_letters(user["sub"])

    return {"status": "updated"}

# ============================================================
# OWNER REQUEST PASSWORD RESET
# ============================================================
@router.post("/request-password-reset")
async def owner_request_password_reset(payload: OwnerResetRequest):

    email = payload.email.lower()

    owner = await users_collection.find_one({
        "email": email,
        "role": "owner"
    })

    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    # 🔒 Rate limit: 5 reset attempts per hour
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)

    attempts = await otp_collection.count_documents({
        "email": email,
        "type": "password_reset",
        "created_at": {"$gte": one_hour_ago}
    })

    if attempts >= 5:
        raise HTTPException(
            status_code=429,
            detail="Maximum password reset attempts reached. Try again later."
        )

    otp = randint(100000, 999999)

    expiry = datetime.utcnow() + timedelta(minutes=10)

    await otp_collection.insert_one({
        "email": email,
        "otp": otp,
        "type": "password_reset",
        "expires": expiry,
        "created_at": datetime.utcnow()
    })

    # Send email
    try:
        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)

        message = Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=email,
            subject="Orderly Affairs Password Reset",
            html_content=f"""
            <div style="font-family:Arial">
                <h3>Password Reset Request</h3>
                <p>Your password reset code:</p>
                <h2>{otp}</h2>
                <p>This code expires in 10 minutes.</p>
            </div>
            """
        )

        sg.send(message)

    except Exception as e:
        print("SendGrid error:", e)

    return {"message": "Password reset OTP sent"}

# ============================================================
# OWNER RESET PASSWORD
# ============================================================
@router.post("/reset-password")
async def owner_reset_password(payload: OwnerResetPassword):

    email = payload.email.lower()

    owner = await users_collection.find_one({
        "email": email,
        "role": "owner"
    })

    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    record = await otp_collection.find_one({
        "email": email,
        "otp": payload.otp,
        "type": "password_reset"
    })

    if not record:
        raise HTTPException(status_code=400, detail="Invalid OTP")

    if datetime.utcnow() > record["expires"]:
        raise HTTPException(status_code=400, detail="OTP expired")

    # 🔒 Hash new password
    hashed_password = hash_password(payload.new_password)

    await users_collection.update_one(
        {"email": email},
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

    return {
        "message": "Password reset successful"
    }

# ============================================================
# 2️⃣ START SMS MFA (ONLY if phone missing or manual trigger)
# ============================================================

@router.post("/start-sms-mfa")
async def start_sms_mfa(
    payload: StartSMSMFARequest,
    authorization: str | None = Header(default=None),
):
    user = await users_collection.find_one({
        "email": payload.email.lower().strip(),
        "role": "owner"
    })

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    phone = user.get("phone")
    methods = normalize_mfa_methods(user)
    authorized_owner = await get_authorized_owner_for_email(
        payload.email.lower().strip(),
        authorization,
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

        await users_collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"phone": phone, "updated_at": datetime.utcnow()}}
        )

    try:
        await enforce_sms_resend_cooldown(phone)
        send_verification_code(phone)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "requires_phone": False,
        "phone": phone,
        "message": "OTP sent"
    }
# ============================================================
# 3️⃣ RESEND OTP (CLEAN)
# ============================================================

@router.post("/resend-sms-mfa")
async def resend_sms_mfa(payload: EmailRequest):
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

        await enforce_sms_resend_cooldown(phone)

        try:
            send_verification_code(phone)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        return {
            "message": "Signup OTP resent successfully",
            "phone": phone
        }

    # real user login flow
    user = await users_collection.find_one({
        "email": email,
        "role": "owner"
    })

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    methods = normalize_mfa_methods(user)
    if not methods["sms"]:
        raise HTTPException(status_code=400, detail="SMS MFA not enabled")

    phone = user.get("phone")
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number not configured")

    await enforce_sms_resend_cooldown(phone)

    try:
        send_verification_code(phone)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "message": "OTP resent successfully",
        "phone": phone
    }
# ============================================================
# 4️⃣ VERIFY OTP (FINAL LOGIN STEP)
# ============================================================

@router.post("/verify-sms-otp")
async def verify_sms_otp(
    payload: VerifySMSOTPRequest,
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

        try:
            result = check_verification_code(phone, otp)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        if result.status != "approved":
            raise HTTPException(
                status_code=400,
                detail=f"Verification failed: {result.status}"
            )

        created_user = await create_real_user_from_pending(pending)
        token = create_access_token(created_user)

        return {
            "access_token": token,
            "message": "Signup SMS verification successful"
        }

    # otherwise normal login MFA
    user = await users_collection.find_one({
        "email": email,
        "role": "owner"
    })

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    methods = normalize_mfa_methods(user)
    authorized_owner = await get_authorized_owner_for_email(email, authorization)
    if not methods["sms"] and not authorized_owner:
        raise HTTPException(
            status_code=403,
            detail="SMS MFA is not linked. Sign in and enable it from Vault Settings."
        )

    phone = user.get("phone")
    if not phone:
        raise HTTPException(status_code=400, detail="Phone number not configured")

    try:
        result = check_verification_code(phone, otp)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if result.status != "approved":
        raise HTTPException(
            status_code=400,
            detail=f"Verification failed: {result.status}"
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
    token = create_access_token(updated_user)

    return {
        "access_token": token,
        "message": "Login SMS verification successful"
    }
# ============================================================
# 5️⃣ LINK PHONE (ENABLE SMS MFA)
# ============================================================

@router.post("/link-sms")
async def link_sms(payload: PhoneRequest, authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = await users_collection.find_one({
        "email": decoded["sub"],
        "role": "owner"
    })

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        phone = format_phone(payload.phoneNumber)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

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
async def resume_pending_signup(payload: EmailRequest):
    email = payload.email.lower().strip()

    pending = await pending_signup_collection.find_one({
        "email": email,
        "expires_at": {"$gt": datetime.utcnow()}
    })

    if not pending:
        raise HTTPException(status_code=404, detail="No pending signup found")

    method = pending.get("mfa_method")

    if method == "authenticator":
        secret = pending.get("provisioned_secret")
        if not secret:
            raise HTTPException(status_code=400, detail="Authenticator setup not available")

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
            "secret": secret,
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
            "phone": pending.get("phone"),
        }

    raise HTTPException(status_code=400, detail="Invalid pending signup state")
