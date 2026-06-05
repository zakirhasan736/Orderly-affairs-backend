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

    if role == "nextkin":
        sub = str(user_data["_id"])
    else:
        sub = user_data["email"]

    payload = {
        "sub": sub,
        "role": role,
        "email": user_data.get("email"),
        "owner_id": str(user_data.get("owner_id") or user_data.get("_id")),
        "exp": expire,
    }

    private_key = settings.JWT_PRIVATE_KEY.replace("\\n", "\n")

    return jwt.encode(
        payload,
        private_key,
        algorithm=settings.JWT_ALGORITHM,
    )


def verify_token(token: str):
    try:
        public_key = settings.JWT_PUBLIC_KEY.replace("\\n", "\n")

        payload = jwt.decode(
            token,
            public_key,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except Exception:
        return None


def create_mfa_challenge_token(email: str, expires_minutes: int = 10) -> str:
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    payload = {
        "sub": email.lower().strip(),
        "purpose": "mfa_login",
        "exp": expire,
    }
    private_key = settings.JWT_PRIVATE_KEY.replace("\\n", "\n")
    return jwt.encode(payload, private_key, algorithm=settings.JWT_ALGORITHM)


def verify_mfa_challenge_token(token: str | None, email: str) -> bool:
    if not token or not token.strip():
        return False

    payload = verify_token(token.strip())
    if not payload:
        return False

    if payload.get("purpose") != "mfa_login":
        return False

    return payload.get("sub", "").lower() == email.lower().strip()
