"""Generate VAPID keys for Web Push.

Usage:
  python scripts/generate_vapid_keys.py

Copy PUBLIC into:
  - backend .env  VAPID_PUBLIC_KEY=...
  - frontend .env NEXT_PUBLIC_VAPID_PUBLIC_KEY=...

Copy PRIVATE PEM into backend .env as a single line or quoted multiline:
  VAPID_PRIVATE_KEY=\"-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n\"
"""

from __future__ import annotations

import base64
import sys

try:
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01
except ImportError:
    print("Install deps first: pip install pywebpush", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    vapid = Vapid01()
    vapid.generate_keys()
    public_raw = vapid.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = base64.urlsafe_b64encode(public_raw).decode().rstrip("=")
    private_pem = vapid.private_pem()
    if isinstance(private_pem, bytes):
        private_pem = private_pem.decode()

    print("# --- Web Push VAPID ---")
    print(f"VAPID_PUBLIC_KEY={public_b64}")
    print(f"NEXT_PUBLIC_VAPID_PUBLIC_KEY={public_b64}")
    print("VAPID_SUBJECT=mailto:support@orderly-affairs.com")
    print("VAPID_PRIVATE_KEY=" + private_pem.replace("\n", "\\n"))
    print()
    print("# Private PEM (readable):")
    print(private_pem)


if __name__ == "__main__":
    main()
