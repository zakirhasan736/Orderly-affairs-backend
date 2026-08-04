"""
Migrate legacy AI document security gaps.

1) Encrypt plaintext ai_documents.extracted_text (AES-256-GCM)
2) Re-upload public Cloudinary AI assets as authenticated media

Usage:
  python scripts/migrate_ai_security.py --dry-run
  python scripts/migrate_ai_security.py
  python scripts/migrate_ai_security.py --extracts-only
  python scripts/migrate_ai_security.py --cloudinary-only --limit 50
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

from app.ai.ai_security_migration import run_ai_security_migration


async def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate AI OCR + Cloudinary security")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count / plan only; do not write Mongo or Cloudinary",
    )
    parser.add_argument(
        "--extracts-only",
        action="store_true",
        help="Only encrypt extracted_text",
    )
    parser.add_argument(
        "--cloudinary-only",
        action="store_true",
        help="Only privatize Cloudinary assets",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max Cloudinary docs to process",
    )
    args = parser.parse_args()

    extracts = not args.cloudinary_only
    cloudinary = not args.extracts_only

    print("\n=== AI security migration ===")
    print(f"dry_run={args.dry_run} extracts={extracts} cloudinary={cloudinary}\n")

    result = await run_ai_security_migration(
        dry_run=args.dry_run,
        extracts=extracts,
        cloudinary=cloudinary,
        cloudinary_limit=args.limit,
    )
    print(result)

    extract_failed = (result.get("extracted_text") or {}).get("failed", 0)
    cloud_failed = (result.get("cloudinary") or {}).get("failed", 0)
    if extract_failed or cloud_failed:
        print("\nWARNING: some rows failed — review errors and re-run.\n")
        return 1

    print("\nDone.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
