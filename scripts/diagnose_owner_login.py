"""
Run on the production server (inside the backend venv) to see why owner login 401s.

Usage:
  cd /var/www/backend
  source venv/bin/activate
  python scripts/diagnose_owner_login.py you@email.com 'YourPassword'

Does not print the password or full hash.
"""

from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from app.database import users_collection
from app.security.password_handler import verify_password


async def main(email: str, password: str) -> int:
    email = email.lower().strip()
    user = await users_collection.find_one({"email": email})

    if not user:
        print(f"RESULT: no user document for email={email!r}")
        return 1

    role = user.get("role")
    hash_value = user.get("password") or ""
    hash_alt = user.get("password_hash") or ""
    prefix = (hash_value or hash_alt)[:28] or "(empty)"

    print(f"email={email!r}")
    print(f"role={role!r}  (login requires role='owner')")
    print(f"has password field={bool(hash_value)}")
    print(f"has password_hash field={bool(hash_alt)}")
    print(f"hash_prefix={prefix!r}")

    if role != "owner":
        print("RESULT: user exists but role is not owner — /auth/login will 401")
        return 2

    stored = hash_value or hash_alt
    ok = verify_password(password, stored)
    print(f"verify_password={ok}")
    if not ok:
        print("RESULT: password does not match stored hash (or hash format unsupported)")
        return 3

    print("RESULT: credentials OK — if API still 401, redeploy app code / check captcha")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(64)
    raise SystemExit(asyncio.run(main(sys.argv[1], sys.argv[2])))
