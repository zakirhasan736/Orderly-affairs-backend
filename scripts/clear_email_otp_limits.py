"""
Clear email OTP send cooldowns / verify locks so users can request a new code.

  cd /var/www/backend && source venv/bin/activate
  python scripts/clear_email_otp_limits.py you@email.com
"""

from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from app.database import otp_fraud_logs_collection, otp_verify_locks_collection


async def main(email: str) -> int:
    email = email.lower().strip()
    r1 = await otp_fraud_logs_collection.delete_many(
        {"channel": "email", "email": email, "action": "send"}
    )
    r2 = await otp_verify_locks_collection.delete_many(
        {"key": {"$in": [f"email|{email}", f"password_reset|{email}"]}}
    )
    print(f"cleared send logs={r1.deleted_count} locks={r2.deleted_count} for {email}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(64)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
