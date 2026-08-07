"""Optional first-boot admin bootstrap — never ships a hardcoded password."""

from __future__ import annotations

from datetime import datetime

from app.billing.access import default_billing_fields
from app.config import settings
from app.database import users_collection
from app.security.password_handler import hash_password

_WEAK_PASSWORDS = frozenset(
    {
        "admin@123456",
        "admin123",
        "password",
        "changeme",
        "admin@123456//",
        "orderly",
        "orderlyaffairs",
    }
)


def _clean_bootstrap_password(raw: str) -> str:
    """Strip accidental comment / slash junk from thin .env values."""
    value = (raw or "").strip()
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    if "#" in value and " " not in value.split("#", 1)[0]:
        # rare: PASSWORD=secret#comment with no space
        pass
    while value.endswith("//"):
        value = value[:-2].rstrip()
    return value.strip()


def _is_weak_bootstrap_password(password: str) -> bool:
    lowered = password.lower()
    if lowered in _WEAK_PASSWORDS:
        return True
    if password == "Admin@123456":
        return True
    if len(password) < 12:
        return True
    return False


async def seed_default_admin() -> None:
    """
    Create a system-owner admin only when ADMIN_DEFAULT_EMAIL + ADMIN_DEFAULT_PASSWORD
    are both set in the environment.

    Never overwrites an existing password unless ADMIN_DEFAULT_RESET_PASSWORD=true.
    Never uses hardcoded credentials.
    """
    email = (settings.ADMIN_DEFAULT_EMAIL or "").strip().lower()
    password = _clean_bootstrap_password(settings.ADMIN_DEFAULT_PASSWORD or "")
    reset_password = bool(getattr(settings, "ADMIN_DEFAULT_RESET_PASSWORD", False))

    if not email and not password:
        return

    if not email or not password:
        print(
            "[admin] Skipping bootstrap: set both ADMIN_DEFAULT_EMAIL and "
            "ADMIN_DEFAULT_PASSWORD, or leave both unset."
        )
        return

    if _is_weak_bootstrap_password(password):
        print(
            "[admin] Refusing weak ADMIN_DEFAULT_PASSWORD — "
            "use 12+ chars (not Admin@123456 / common demos), or leave unset."
        )
        return

    now = datetime.utcnow()
    existing = await users_collection.find_one({"email": email, "role": "owner"})

    if existing:
        updates: dict = {
            "is_admin": True,
            "role_admin": True,
            "admin_role": existing.get("admin_role") or "super_admin",
            "admin_areas": existing.get("admin_areas") or ["*"],
            "suspended": False,
            "access_revoked": False,
            "updated_at": now,
        }
        if reset_password:
            updates["password"] = hash_password(password)
        await users_collection.update_one(
            {"_id": existing["_id"]},
            {
                "$set": updates,
                "$unset": {"deleted_at": ""},
            },
        )
        if reset_password:
            print(
                f"[admin] Reset bootstrap admin password: {email} "
                "(set ADMIN_DEFAULT_RESET_PASSWORD=false and remove "
                "ADMIN_DEFAULT_PASSWORD from env; enable admin MFA)"
            )
        elif settings.is_development:
            print(f"[admin] Admin flags ensured (password unchanged): {email}")
        return

    doc = {
        "email": email,
        "password": hash_password(password),
        "full_name": "System Owner",
        "name": "System Owner",
        "role": "owner",
        "verified": True,
        "mfa_enabled": False,
        "is_admin": True,
        "role_admin": True,
        "admin_role": "super_admin",
        "admin_areas": ["*"],
        "admin_mfa_enabled": False,
        "billing": {
            **default_billing_fields(),
            "status": "complimentary",
            "plan": "complimentary",
            "is_trial": False,
            "comp": {
                "enabled": True,
                "kind": "lifetime",
                "starts_at": now,
                "ends_at": None,
                "granted_by": "system_seed",
                "granted_at": now,
                "note": "Bootstrap system owner admin",
                "reminders_sent": [],
            },
        },
        "created_at": now,
        "updated_at": now,
    }
    await users_collection.insert_one(doc)
    print(
        f"[admin] Created bootstrap admin: {email} "
        "(remove ADMIN_DEFAULT_PASSWORD from env; enable admin MFA)"
    )
