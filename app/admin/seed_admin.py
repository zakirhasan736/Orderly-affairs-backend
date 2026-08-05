"""Optional first-boot admin bootstrap — never ships a hardcoded password."""

from __future__ import annotations

from datetime import datetime

from app.billing.access import default_billing_fields
from app.config import settings
from app.database import users_collection
from app.security.password_handler import hash_password


async def seed_default_admin() -> None:
    """
    Create a system-owner admin only when ADMIN_DEFAULT_EMAIL + ADMIN_DEFAULT_PASSWORD
    are both set in the environment and that email does not exist yet.

    Never overwrites an existing password. Never uses hardcoded credentials.
    """
    email = (settings.ADMIN_DEFAULT_EMAIL or "").strip().lower()
    password = (settings.ADMIN_DEFAULT_PASSWORD or "").strip()

    if not email and not password:
        return

    if not email or not password:
        print(
            "[admin] Skipping bootstrap: set both ADMIN_DEFAULT_EMAIL and "
            "ADMIN_DEFAULT_PASSWORD, or leave both unset."
        )
        return

    # Refuse known weak / documented demo passwords even if set in env.
    weak = {"admin@123456", "admin123", "password", "changeme"}
    if password.lower() in weak or password == "Admin@123456":
        print(
            "[admin] Refusing weak ADMIN_DEFAULT_PASSWORD — "
            "set a strong unique password or leave unset."
        )
        return

    now = datetime.utcnow()
    existing = await users_collection.find_one({"email": email, "role": "owner"})

    if existing:
        # Keep current password. Only ensure admin flags stay intact.
        await users_collection.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "is_admin": True,
                    "role_admin": True,
                    "admin_role": existing.get("admin_role") or "super_admin",
                    "admin_areas": existing.get("admin_areas") or ["*"],
                    "suspended": False,
                    "access_revoked": False,
                    "updated_at": now,
                },
                "$unset": {"deleted_at": ""},
            },
        )
        if settings.is_development:
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
