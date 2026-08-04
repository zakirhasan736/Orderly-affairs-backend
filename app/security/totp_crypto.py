"""Encrypt TOTP secrets at rest (users + pending signup)."""

from app.security.crypto import decrypt_data, encrypt_data, is_encrypted_payload


def encrypt_totp_value(email: str, secret: str, *, pending: bool = False) -> str:
    scope = f"pending_totp:{email.lower().strip()}" if pending else f"totp:{email.lower().strip()}"
    return encrypt_data({"s": secret}, context=scope)


def decrypt_totp_value(email: str, value: str, *, pending: bool = False) -> str:
    if not value:
        return ""
    if not is_encrypted_payload(value):
        return value
    scope = f"pending_totp:{email.lower().strip()}" if pending else f"totp:{email.lower().strip()}"
    return decrypt_data(value, context=scope).get("s", "")


def encrypt_admin_totp_value(email: str, secret: str) -> str:
    scope = f"admin_totp:{email.lower().strip()}"
    return encrypt_data({"s": secret}, context=scope)


def decrypt_admin_totp_value(email: str, value: str) -> str:
    if not value:
        return ""
    if not is_encrypted_payload(value):
        return value
    scope = f"admin_totp:{email.lower().strip()}"
    return decrypt_data(value, context=scope).get("s", "")


def read_user_totp_secret(user: dict) -> str | None:
    raw = user.get("totp_secret")
    if not raw:
        return None
    email = user.get("email") or ""
    return decrypt_totp_value(email, raw) or None


def read_admin_totp_secret(user: dict) -> str | None:
    """Prefer encrypted admin field; fall back to plaintext admin_totp_secret."""
    email = user.get("email") or ""
    enc = user.get("admin_totp_secret_enc")
    if enc:
        return decrypt_admin_totp_value(email, enc) or None
    raw = user.get("admin_totp_secret")
    if not raw:
        return None
    if is_encrypted_payload(raw):
        return decrypt_admin_totp_value(email, raw) or None
    return raw


def write_admin_totp_fields(email: str, secret: str) -> dict:
    enc = encrypt_admin_totp_value(email, secret)
    return {
        "admin_totp_secret_enc": enc,
        "admin_totp_secret": enc,
    }


def read_pending_totp_secret(pending: dict) -> str | None:
    raw = pending.get("provisioned_secret")
    if not raw:
        return None
    email = pending.get("email") or ""
    return decrypt_totp_value(email, raw, pending=True) or None


def read_user_provisioned_secret(user: dict) -> str | None:
    raw = user.get("provisioned_secret")
    if not raw:
        return None
    email = user.get("email") or ""
    return decrypt_totp_value(email, raw, pending=True) or None
