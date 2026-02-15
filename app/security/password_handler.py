# from passlib.context import CryptContext

# pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# def hash_password(password: str) -> str:
#     # bcrypt only supports up to 72 bytes
#     password = password.encode("utf-8")[:72].decode("utf-8")
#     return pwd_context.hash(password)

# def verify_password(plain_password: str, hashed_password: str) -> bool:
#     plain_password = plain_password.encode("utf-8")[:72].decode("utf-8")
#     return pwd_context.verify(plain_password, hashed_password)
import hashlib
from passlib.context import CryptContext

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)

def _normalize_password(password: str) -> bytes:
    """
    Hash password with SHA-256 first.
    Output is always 32 bytes (safe for bcrypt).
    """
    return hashlib.sha256(password.encode("utf-8")).digest()

def hash_password(password: str) -> str:
    normalized = _normalize_password(password)
    return pwd_context.hash(normalized)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    normalized = _normalize_password(plain_password)
    return pwd_context.verify(normalized, hashed_password)
