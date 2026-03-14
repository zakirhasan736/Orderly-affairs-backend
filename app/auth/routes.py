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
from app.database import users_collection, otp_collection
from app.security.billing_guard import enforce_billing
from app.security.password_handler import hash_password, verify_password
from app.security.jwt_handler import create_access_token, verify_token
from app.auth.utils import generate_and_send_otp, verify_otp
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

# ---- Next-of-Kin ----
class NextKinCreateRequest(BaseModel):
    email: EmailStr
    full_name: str
    relationship: str
    phone_number: str | None = None
    access_level: str = "full" 
    authorized_sections: list[str] | None = []
    immediate_access: bool | None = False
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
    master_password: str | None = None
    password_card_generated: bool | None = None
    card_storage_location: str | None = None
    special_instructions: str | None = None

class NextKinLoginRequest(BaseModel):
    email: EmailStr
    master_password: str


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
        {"$set": {"immediate_access": True, "updated_at": datetime.utcnow()}},
    )

    await send_nextkin_email(
        event=NextKinEmailEvent.ACCESS_APPROVED,
        nextkin=nextkin,
        owner=owner,
        plain_password=plain_password,
    )

# Helper to flip immediate_access and notify the Next-of-Kin
async def _approve_and_notify_if_needed(nextkin: dict, owner: dict, approved: bool = True):
    if nextkin.get("immediate_access", False):
        return  # already approved → no duplicate email

    await users_collection.update_one(
        {"_id": nextkin["_id"]},
        {
            "$set": {
                "immediate_access": approved,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    try:
        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        message = Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=nextkin["email"],
            subject="Orderly Affairs – Immediate Access Granted",
            html_content=f"""
            <div style="font-family:Arial,sans-serif">
              <p>Hello {nextkin.get("full_name")},</p>
              <p>
                <b>{owner.get("full_name") or owner["email"]}</b>
                has granted you <b>Immediate Access</b> to their Orderly Affairs Kit.
              </p>
              <p>You may now log in and view the permitted sections.</p>
              <hr/>
              <small>{datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}</small>
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
    email = user.email.lower()
    existing = await users_collection.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    hashed_pw = hash_password(user.password)
    new_user = {
        "email": email,
        "password": hashed_pw,
        "full_name": user.full_name,
        "role": "owner",
        "owner_id": None,
        "verified": False,
        "totp_secret": None,
        "provisioned_secret": None,
        "mfa_linked": False,
        "mfa_enabled": False,
        "primary_mfa": None,
        "mfa_methods": {
            "email": False,
            "authenticator": False,
            "sms": False,
        },
        "billing": {
        "customer_id": None,
        "subscription_id": None,
        "status": "pending",          # pending | trialing | active | past_due | blocked
        "plan": None,                 # monthly | yearly
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
    }

    await users_collection.insert_one(new_user)

    return {"message": "Owner account created successfully. Please set up MFA after login."}


# ============================================================
# 2️⃣ OWNER LOGIN
# ============================================================
@router.post("/login")
async def owner_login(data: LoginRequest):
    email = data.email.lower()
    user = await users_collection.find_one({"email": email, "role": "owner"})
    if not user:
        raise HTTPException(status_code=404, detail="Owner not found")
    if not verify_password(data.password, user["password"]):
        raise HTTPException(status_code=400, detail="Invalid credentials")
    billing = user.get("billing", {})
    return {
        "message": "Password verified",
        "email": email,
        "role": "owner",
        "mfa_enabled": user.get("mfa_enabled", False),
        "primary_mfa": user.get("primary_mfa"),
        "mfa_methods": user.get("mfa_methods", {}),
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
                {"$set": {"immediate_access": True, "updated_at": datetime.utcnow()}},
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
    if user.get("primary_mfa") != "authenticator":
        raise HTTPException(status_code=403, detail="Authenticator MFA not enabled")

    await users_collection.update_one(
        {"email": payload.email.lower()},
        {
            "$set": {
                "verified": True,
                "mfa_enabled": True,
                "primary_mfa": "authenticator",
                "mfa_methods.authenticator": True,
                "updated_at": datetime.utcnow(),
            }
        },
    )


    token = create_access_token(user)
    return {"access_token": token, "message": "Login successful"}


# ============================================================
# 7️⃣ GENERATE MFA QR
# ============================================================
@router.post("/generate-mfa")
async def generate_mfa(payload: EmailRequest):
    user = await users_collection.find_one({"email": payload.email.lower()})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("mfa_linked"):
        raise HTTPException(status_code=400, detail="Authenticator already linked")

    secret = pyotp.random_base32()
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=payload.email, issuer_name="Orderly Affairs")
    qr = qrcode.make(uri)
    buf = BytesIO()
    qr.save(buf, format="PNG")
    img_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    await users_collection.update_one({"email": payload.email.lower()}, {"$set": {"provisioned_secret": secret}})
    return {"qrCodeUrl": f"data:image/png;base64,{img_base64}", "secret": secret}


# ============================================================
# 8️⃣ LINK AUTHENTICATOR
# ============================================================
@router.post("/link-authenticator")
async def link_authenticator(payload: LinkAuthenticatorRequest):
    user = await users_collection.find_one({"email": payload.email.lower()})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    totp = pyotp.TOTP(payload.secret)
    if not totp.verify(payload.code):
        raise HTTPException(status_code=400, detail="Invalid verification code")
    await users_collection.update_one(
    {"email": payload.email.lower()},
    {
        "$set": {
            "totp_secret": payload.secret,
            "provisioned_secret": None,
            "mfa_linked": True,
            "mfa_enabled": True,
            "verified": True,
            "mfa_method": "authenticator",
            "updated_at": datetime.utcnow(),
        }
    },
)

    token = create_access_token(user)
    return {"access_token": token, "message": "Authenticator linked successfully"}


# ============================================================
# 9️⃣ EMAIL OTP — SEND & VERIFY
# ============================================================
@router.post("/send-email")
async def send_email_otp(payload: EmailRequest):
    otp = randint(100000, 999999)
    expiry = datetime.utcnow() + timedelta(minutes=10)
    await otp_collection.delete_many({"email": payload.email})
    await otp_collection.insert_one({"email": payload.email, "otp": otp, "expires": expiry})
    try:
        sg = sendgrid.SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        message = Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=payload.email,
            subject="Your Orderly Affairs verification code",
            html_content=f"<p>Your verification code is <b>{otp}</b>. It expires in 10 minutes.</p>",
        )
        sg.send(message)
    except Exception as e:
        print("SendGrid Error:", e)
    return {"message": f"Verification code sent to {payload.email}"}


@router.post("/verify-email")
async def verify_email_code(payload: VerifyEmailRequest):
    record = await otp_collection.find_one({"email": payload.email})
    if not record:
        raise HTTPException(status_code=400, detail="No OTP found")

    if datetime.utcnow() > record["expires"]:
        raise HTTPException(status_code=400, detail="OTP expired")

    if record["otp"] != payload.code:
        raise HTTPException(status_code=400, detail="Invalid OTP")

    # ✅ load user AFTER otp validation
    user = await users_collection.find_one({
    "email": payload.email,
    "role": "owner"
    })

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # ❌ REMOVE mfa_method guard completely here
    # Email verification itself ENABLES email MFA

    await otp_collection.delete_many({"email": payload.email})

    await users_collection.update_one(
        {"email": payload.email},
        {
            "$set": {
                "verified": True,
                "mfa_enabled": True,
                "primary_mfa": "email",
                "mfa_methods.email": True,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    token = create_access_token(user)
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
        "role": user.get("role", "owner"),
        "mfa_enabled": user.get("mfa_enabled", False),
        "primary_mfa": user.get("primary_mfa"),
        "mfa_methods": user.get("mfa_methods", {}),
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