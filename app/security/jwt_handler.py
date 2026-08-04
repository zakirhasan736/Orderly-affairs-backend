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


def _normalize_pem(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.replace("\\n", "\n").strip()
    return normalized or None


def _verification_public_keys() -> list[str]:
    keys: list[str] = []
    current = _normalize_pem(settings.JWT_PUBLIC_KEY)
    if current:
        keys.append(current)
    previous = _normalize_pem(getattr(settings, "JWT_PREVIOUS_PUBLIC_KEY", None))
    if previous and previous not in keys:
        keys.append(previous)
    return keys


def create_access_token(
    user_data: dict,
    expires_delta: timedelta | None = None,
    *,
    role: str | None = None,
):
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    resolved_role = role or user_data.get("role", "owner")

    if resolved_role == "nextkin":
        sub = str(user_data["_id"])
    else:
        # owner + admin sessions use email as subject
        sub = user_data["email"]

    payload = {
        "sub": sub,
        "role": resolved_role,
        "email": user_data.get("email"),
        "owner_id": str(user_data.get("owner_id") or user_data.get("_id")),
        "exp": expire,
    }
    if resolved_role == "admin":
        payload["admin_role"] = user_data.get("admin_role") or "system_owner"

    private_key = _normalize_pem(settings.JWT_PRIVATE_KEY)
    if not private_key:
        raise RuntimeError("JWT_PRIVATE_KEY is not configured")

    return jwt.encode(
        payload,
        private_key,
        algorithm=settings.JWT_ALGORITHM,
    )


def verify_token(token: str):
    """Verify RS256 JWT against current public key, then previous during rotation."""
    for public_key in _verification_public_keys():
        try:
            return jwt.decode(
                token,
                public_key,
                algorithms=[settings.JWT_ALGORITHM],
            )
        except Exception:
            continue
    return None


def create_mfa_challenge_token(email: str, expires_minutes: int = 10) -> str:
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    payload = {
        "sub": email.lower().strip(),
        "purpose": "mfa_login",
        "exp": expire,
    }
    private_key = _normalize_pem(settings.JWT_PRIVATE_KEY)
    if not private_key:
        raise RuntimeError("JWT_PRIVATE_KEY is not configured")
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


def create_step_up_token(email: str, expires_minutes: int = 10) -> str:
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    payload = {
        "sub": email.lower().strip(),
        "purpose": "step_up",
        "exp": expire,
    }
    private_key = _normalize_pem(settings.JWT_PRIVATE_KEY)
    if not private_key:
        raise RuntimeError("JWT_PRIVATE_KEY is not configured")
    return jwt.encode(payload, private_key, algorithm=settings.JWT_ALGORITHM)


def verify_step_up_token(token: str | None, email: str) -> bool:
    if not token or not token.strip():
        return False

    payload = verify_token(token.strip())
    if not payload:
        return False

    if payload.get("purpose") != "step_up":
        return False

    return payload.get("sub", "").lower() == email.lower().strip()


ADMIN_MFA_LOGIN_PURPOSE = "admin_mfa_login"
ADMIN_MFA_SETUP_PURPOSE = "admin_mfa_setup"


def create_admin_mfa_challenge_token(
    email: str,
    *,
    purpose: str = ADMIN_MFA_LOGIN_PURPOSE,
    expires_minutes: int = 10,
) -> str:
    expire = datetime.utcnow() + timedelta(minutes=expires_minutes)
    payload = {
        "sub": email.lower().strip(),
        "purpose": purpose,
        "exp": expire,
    }
    private_key = _normalize_pem(settings.JWT_PRIVATE_KEY)
    if not private_key:
        raise RuntimeError("JWT_PRIVATE_KEY is not configured")
    return jwt.encode(payload, private_key, algorithm=settings.JWT_ALGORITHM)


def verify_admin_mfa_challenge_token(
    token: str | None,
    email: str,
    *,
    purpose: str = ADMIN_MFA_LOGIN_PURPOSE,
) -> bool:
    if not token or not token.strip():
        return False

    payload = verify_token(token.strip())
    if not payload:
        return False

    if payload.get("purpose") != purpose:
        return False

    return payload.get("sub", "").lower() == email.lower().strip()
