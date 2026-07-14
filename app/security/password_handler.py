import hashlib

from passlib.context import CryptContext

pwd_context = CryptContext(
    schemes=["argon2", "bcrypt"],
    deprecated="auto",
)


def _normalize_password(password: str) -> bytes:
    """Pre-hash so long passwords work under bcrypt's 72-byte limit."""
    return hashlib.sha256(password.encode("utf-8")).digest()


def _legacy_plain_password(password: str) -> str:
    """Pre-migration hashes used truncated plain-text passwords."""
    return password.encode("utf-8")[:72].decode("utf-8", errors="ignore")


def hash_password(password: str) -> str:
    return pwd_context.hash(_normalize_password(password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False

    # Current: argon2/bcrypt of SHA-256 digest
    try:
        if pwd_context.verify(_normalize_password(plain_password), hashed_password):
            return True
    except (ValueError, TypeError):
        pass

    # Legacy: argon2/bcrypt of plain (truncated) password string
    try:
        return pwd_context.verify(
            _legacy_plain_password(plain_password),
            hashed_password,
        )
    except (ValueError, TypeError):
        return False
