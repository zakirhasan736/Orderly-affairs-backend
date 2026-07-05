# app/ai/ai_auth.py

from fastapi import Header, HTTPException, Request
from bson import ObjectId
from bson.errors import InvalidId

from app.database import users_collection
from app.security.jwt_handler import verify_token
from app.security.cookie_auth import OWNER_ACCESS_COOKIE, extract_access_token


async def get_current_owner(
    request: Request,
    authorization: str | None = Header(default=None),
):
    token = extract_access_token(
        request,
        authorization,
        access_cookie=OWNER_ACCESS_COOKIE,
        required=True,
    )

    decoded = verify_token(token)

    if not decoded:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    sub = decoded.get("sub")
    role = decoded.get("role", "owner")

    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    if role != "owner":
        raise HTTPException(status_code=403, detail="Only owner users can use AI autofill")

    query_options = [
        {"email": sub, "role": "owner"},
        {"email": sub},
    ]

    try:
        query_options.append({"_id": ObjectId(sub), "role": "owner"})
        query_options.append({"_id": ObjectId(sub)})
    except InvalidId:
        pass

    user = await users_collection.find_one({"$or": query_options})

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


def get_user_id(current_user) -> str:
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = current_user.get("_id") or current_user.get("id") or current_user.get("user_id")

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user")

    return str(user_id)
