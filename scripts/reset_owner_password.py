"""
Reset an owner password on the server (uses current hash_password).

Usage:
  cd /var/www/backend
  source venv/bin/activate
  python scripts/reset_owner_password.py you@email.com 'NewStrongPassword1!'
"""

from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from app.database import users_collection
from app.security.password_handler import hash_password, verify_password


async def main(email: str, new_password: str) -> int:
    email = email.lower().strip()
    if len(new_password) < 8:
        print("Password must be at least 8 characters")
        return 1

    user = await users_collection.find_one({"email": email, "role": "owner"})
    if not user:
        print(f"No owner found for {email!r}")
        return 2

    hashed = hash_password(new_password)
    await users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"password": hashed}},
    )

    ok = verify_password(new_password, hashed)
    print(f"Updated password for {email!r}")
    print(f"hash_prefix={hashed[:28]!r}")
    print(f"self_verify={ok}")
    return 0 if ok else 3


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(64)
    raise SystemExit(asyncio.run(main(sys.argv[1], sys.argv[2])))
