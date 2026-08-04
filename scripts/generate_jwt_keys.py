"""
Generate a new RS256 JWT key pair for Orderly Affairs.

Writes:
  keys/private.pem
  keys/public.pem

If keys already exist, moves the current public key to keys/public.previous.pem
so verify_token can accept tokens signed by the old private key during overlap.

Usage:
  python scripts/generate_jwt_keys.py
  python scripts/generate_jwt_keys.py --force
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]
KEYS_DIR = ROOT / "keys"
PRIVATE_PATH = KEYS_DIR / "private.pem"
PUBLIC_PATH = KEYS_DIR / "public.pem"
PREVIOUS_PUBLIC_PATH = KEYS_DIR / "public.previous.pem"


def generate_pair() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private_pem, public_pem


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate JWT RS256 key pair")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing keys (keeps prior public as public.previous.pem)",
    )
    args = parser.parse_args()

    KEYS_DIR.mkdir(parents=True, exist_ok=True)

    if PRIVATE_PATH.exists() and not args.force:
        print(f"Refusing to overwrite {PRIVATE_PATH} (pass --force).")
        return 1

    if PUBLIC_PATH.exists():
        shutil.copy2(PUBLIC_PATH, PREVIOUS_PUBLIC_PATH)
        print(f"Saved previous public key → {PREVIOUS_PUBLIC_PATH}")

    private_pem, public_pem = generate_pair()
    PRIVATE_PATH.write_text(private_pem, encoding="utf-8")
    PUBLIC_PATH.write_text(public_pem, encoding="utf-8")
    try:
        PRIVATE_PATH.chmod(0o600)
    except Exception:
        pass

    print(f"Wrote {PRIVATE_PATH}")
    print(f"Wrote {PUBLIC_PATH}")
    print(
        "\nNext:\n"
        "  1. Deploy new private+public keys; keep public.previous.pem (or JWT_PREVIOUS_PUBLIC_KEY).\n"
        "  2. Restart API — new tokens sign with the new private key; old tokens still verify.\n"
        "  3. After refresh-token lifetime (default 7d) + access TTL, remove previous public key.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
