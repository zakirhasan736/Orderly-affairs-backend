from fastapi import APIRouter, HTTPException, Header
from datetime import datetime, timedelta
from bson import ObjectId
from passlib.context import CryptContext
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
import string, random

from app.models.nextkin_schema import NextKinCreateRequest, NextKinLoginRequest
from app.database import users_collection
from app.security.jwt_handler import create_access_token, verify_token
from app.config import nextkin_login_url, settings
from app.security.nextkin_profile_crypto import prepare_nextkin_profile_for_storage

router = APIRouter(prefix="/nextkin", tags=["Next-of-Kin"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ----------------------------------------------------------
# 🔐 Helper: Random temporary password
# ----------------------------------------------------------
def generate_temp_password(length: int = 10):
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(length))


# ----------------------------------------------------------
# 👩‍👧 Create Next-of-Kin (Owner only)
# ----------------------------------------------------------
@router.post("/create")
async def create_nextkin(payload: NextKinCreateRequest, authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ")[1]
    decoded = verify_token(token)
    if not decoded or decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can create Next-of-Kin")

    owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    email = payload.email.lower()
    existing = await users_collection.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Next-of-Kin already exists")

    temp_password = generate_temp_password()
    hashed_pw = pwd_context.hash(temp_password)

    new_nok = {
        **payload.dict(),
        "email": email,
        "password": hashed_pw,
        "role": "nextkin",
        "owner_id": str(owner["_id"]),
        "verified": True,
        "created_at": datetime.utcnow(),
    }

    result = await users_collection.insert_one(
        prepare_nextkin_profile_for_storage(new_nok)
    )

    try:
        sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        html = f"""
        <div style="font-family:Arial">
          <h3>Hello {payload.full_name},</h3>
          <p>You’ve been added as a Next-of-Kin by <b>{owner.get("full_name")}</b>.</p>
          <p>Email: {email}<br>Password: {temp_password}</p>
          <a href="{nextkin_login_url()}">Login Here</a>
        </div>
        """
        message = Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=email,
            subject="Orderly Affairs - Next-of-Kin Credentials",
            html_content=html,
        )
        sg.send(message)
    except Exception as e:
        print("⚠️ Email error:", e)

    return {"message": "Next-of-Kin created successfully", "id": str(result.inserted_id)}


# ----------------------------------------------------------
# 🔑 Next-of-Kin Login
# ----------------------------------------------------------
@router.post("/login")
async def nextkin_login(data: NextKinLoginRequest):
    email = data.email.lower()
    user = await users_collection.find_one({"email": email, "role": "nextkin"})
    if not user:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found")
    if not pwd_context.verify(data.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid password")

    token = create_access_token(
        user_data={
            "email": user["email"],
            "role": "nextkin",
            "owner_id": user.get("owner_id"),
            "_id": str(user["_id"]),
        },
        expires_delta=timedelta(days=30),
    )

    return {
        "access_token": token,
        "role": "nextkin",
        "owner_id": str(user["owner_id"]),
        "message": "Login successful",
    }


# ----------------------------------------------------------
# 📋 Get all Next-of-Kin for Owner
# ----------------------------------------------------------
@router.get("/list")
async def get_my_nextkin(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")

    token = authorization.split(" ")[1]
    decoded = verify_token(token)
    if not decoded or decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can view Next-of-Kin")

    owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")

    nextkins = users_collection.find({"owner_id": str(owner["_id"]), "role": "nextkin"})
    results = [
        {**nk, "_id": str(nk["_id"])} async for nk in nextkins
    ]
    return results