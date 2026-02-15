# from datetime import datetime, timedelta
# from jose import jwt
# from app.config import settings

# def create_access_token(data: dict, expires_delta: timedelta | None = None):
#     to_encode = data.copy()
#     expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
#     to_encode.update({"exp": expire})
#     encoded_jwt = jwt.encode(to_encode, settings.JWT_PRIVATE_KEY, algorithm=settings.JWT_ALGORITHM)
#     return encoded_jwt

# def verify_token(token: str):
#     try:
#         payload = jwt.decode(token, settings.JWT_PUBLIC_KEY, algorithms=[settings.JWT_ALGORITHM])
#         return payload
#     except Exception:
#         return None
from datetime import datetime, timedelta
from jose import jwt
from app.config import settings

def create_access_token(user_data: dict, expires_delta: timedelta | None = None):
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    role = user_data.get("role", "owner")

    # ✅ ROLE-AWARE subject
    if role == "nextkin":
        sub = str(user_data["_id"])           # ObjectId string
    else:
        sub = user_data["email"]              # owner email

    payload = {
        "sub": sub,
        "role": role,
        "email": user_data.get("email"),
        "owner_id": str(user_data.get("owner_id") or user_data.get("_id")),
        "exp": expire,
    }

    return jwt.encode(
        payload,
        settings.JWT_PRIVATE_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )



def verify_token(token: str):
    try:
        payload = jwt.decode(
            token,
            settings.JWT_PUBLIC_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except Exception:
        return None

