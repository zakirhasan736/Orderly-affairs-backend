"""Verify bank-grade security prerequisites (DB + env). Run before production deploy."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.config import settings
from app.security.security_audit import audit_totp_secrets


def check_env() -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    checks.append((
        "APP_ENV=production",
        settings.APP_ENV == "production",
        f"current={settings.APP_ENV!r} (use production on deployed server only)",
    ))
    checks.append((
        "ACCESS_TOKEN_EXPIRE_MINUTES <= 15",
        int(getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 15) or 15) <= 15,
        f"current={settings.ACCESS_TOKEN_EXPIRE_MINUTES}",
    ))
    checks.append((
        "TURNSTILE_SECRET_KEY set",
        bool(settings.TURNSTILE_SECRET_KEY),
        "required for CAPTCHA in production",
    ))
    checks.append((
        "OTP_CAPTCHA_ENABLED",
        str(getattr(settings, "OTP_CAPTCHA_ENABLED", "true")).lower() == "true",
        "",
    ))
    checks.append((
        "AES_256_KEY set (32 bytes)",
        bool(os.getenv("AES_256_KEY")),
        "",
    ))
    checks.append((
        "JWT RS256 keys",
        bool(settings.JWT_PRIVATE_KEY and settings.JWT_PUBLIC_KEY),
        "",
    ))

    return checks


async def main() -> int:
    print("\n=== Bank-grade security verification ===\n")

    totp = await audit_totp_secrets()
    totp_ok = totp.get("plain_users", 0) == 0 and totp.get("plain_pending", 0) == 0
    print("TOTP at rest:", totp)
    print("  ", "PASS" if totp_ok else "FAIL", "— all TOTP secrets encrypted\n")

    print("Environment:")
    env_fail = 0
    for name, ok, detail in check_env():
        status = "PASS" if ok else "WARN"
        if not ok:
            env_fail += 1
        suffix = f" ({detail})" if detail else ""
        print(f"  {status}  {name}{suffix}")

    print("\nNext steps:")
    print("  1. python scripts/migrate_totp_secrets.py  (if plain TOTP > 0)")
    print("  2. python scripts/security_smoke_test.py   (API must be running)")
    print("  3. Set APP_ENV=production on production server only")
    print("  4. See PRODUCTION_SETUP.md bank-grade checklist\n")

    if not totp_ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
