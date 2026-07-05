"""Encrypt any plaintext TOTP secrets in MongoDB (run once before/at production deploy)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.security.security_audit import audit_totp_secrets
from app.security.totp_migration import run_totp_encryption_migration


async def main() -> int:
    print("\n=== TOTP at-rest encryption migration ===\n")

    before = await audit_totp_secrets()
    print("Before:", before)

    if before.get("plain_users", 0) == 0 and before.get("plain_pending", 0) == 0:
        print("\nNo plaintext TOTP secrets found — nothing to migrate.\n")
        return 0

    result = await run_totp_encryption_migration()
    print("\nMigrated:", result)

    after = await audit_totp_secrets()
    print("After:", after)

    if after.get("plain_users", 0) or after.get("plain_pending", 0):
        print("\nWARNING: Plaintext TOTP values remain. Review failed records.\n")
        return 1

    print("\nAll TOTP secrets are encrypted at rest.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
