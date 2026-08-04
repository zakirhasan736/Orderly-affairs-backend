"""
Re-encrypt vault ciphertext under the current AES_256_KEY.

Prerequisites:
  1. Generate a new key:
       python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
  2. Set in env:
       AES_256_KEY=<new>
       AES_256_KEY_PREVIOUS=<old>
  3. Restart API (dual-key decrypt is live).
  4. Run this script.
  5. After zero failures, remove AES_256_KEY_PREVIOUS and restart.

Usage:
  python scripts/rotate_aes_key.py --dry-run
  python scripts/rotate_aes_key.py
  python scripts/rotate_aes_key.py --allow-without-previous   # rewrite only (same key)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.security.aes_key_rotation import run_aes_key_rotation


async def main() -> int:
    parser = argparse.ArgumentParser(description="Re-encrypt data with current AES key")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-without-previous",
        action="store_true",
        help="Allow run when AES_256_KEY_PREVIOUS is unset (rewrites under current key)",
    )
    args = parser.parse_args()

    print("\n=== AES key rotation re-encrypt ===")
    print(f"dry_run={args.dry_run}\n")

    try:
        result = await run_aes_key_rotation(
            dry_run=args.dry_run,
            require_previous=not args.allow_without_previous,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 2

    for key, value in result.items():
        if isinstance(value, dict):
            print(f"  {key}: {value}")
        else:
            print(f"  {key}: {value}")

    if int(result.get("total_failed") or 0) > 0:
        print("\nWARNING: some rows failed — fix and re-run before dropping the previous key.\n")
        return 1

    if not args.dry_run:
        print(
            "\nDone. After verifying reads, remove AES_256_KEY_PREVIOUS from env and restart.\n"
        )
    else:
        print("\nDry run complete — no writes.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
