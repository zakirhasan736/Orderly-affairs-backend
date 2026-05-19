# app/auth/dependencies.py

from fastapi import Header, HTTPException, Depends
from bson import ObjectId
from bson.errors import InvalidId

from app.database import users_collection
from app.security.jwt_handler import verify_token


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization token")

    parts = authorization.split(" ")

    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = parts[1].strip()

    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization token")

    return token


async def get_current_user(authorization: str | None = Header(default=None)):
    token = _extract_bearer_token(authorization)

    decoded = verify_token(token)

    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    role = decoded.get("role", "owner")
    sub = decoded.get("sub")

    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    if role == "nextkin":
        try:
            user = await users_collection.find_one(
                {"_id": ObjectId(sub), "role": "nextkin"}
            )
        except InvalidId:
            raise HTTPException(status_code=401, detail="Invalid next-of-kin token")
    else:
        user = await users_collection.find_one(
            {"email": sub, "role": "owner"}
        )

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


async def get_current_owner(current_user=Depends(get_current_user)):
    if current_user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can use AI autofill")

    return current_user


def get_user_id(current_user) -> str:
    if not current_user or "_id" not in current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    return str(current_user["_id"])