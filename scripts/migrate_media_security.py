"""
Privatize legacy public Cloudinary message / letter media.

Usage:
  python scripts/migrate_media_security.py --dry-run
  python scripts/migrate_media_security.py
  python scripts/migrate_media_security.py --messages-only --limit 50
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

from app.security.media_security_migration import run_media_security_migration


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Privatize legacy message/letter Cloudinary media"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--messages-only", action="store_true")
    parser.add_argument("--letters-only", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    messages = not args.letters_only
    letters = not args.messages_only

    print("\n=== Media security migration ===")
    print(f"dry_run={args.dry_run} messages={messages} letters={letters}\n")

    result = await run_media_security_migration(
        dry_run=args.dry_run,
        messages=messages,
        letters=letters,
        limit=args.limit,
    )
    print(result)

    msg_failed = (result.get("messages") or {}).get("failed", 0)
    letter_failed = (result.get("letters") or {}).get("failed", 0)
    if msg_failed or letter_failed:
        print("\nWARNING: some rows failed — review errors and re-run.\n")
        return 1

    print("\nDone.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
