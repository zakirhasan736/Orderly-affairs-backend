#!/usr/bin/env python3
"""Run one encrypted user-data backup (local + optional S3).

Usage (from backend repo root, with .env loaded):
  python scripts/run_backup.py
  python scripts/run_backup.py --no-s3
  python scripts/run_backup.py --s3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure repo root is on path when invoked as scripts/run_backup.py
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> int:
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Orderly Affairs encrypted backup")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--s3",
        action="store_true",
        help="Force S3 upload (requires BACKUP_S3_* / AWS credentials)",
    )
    group.add_argument(
        "--no-s3",
        action="store_true",
        help="Skip S3 even if BACKUP_S3_ENABLED=true",
    )
    args = parser.parse_args()

    from app.backup.service import run_daily_backup

    upload_s3: bool | None = None
    if args.s3:
        upload_s3 = True
    elif args.no_s3:
        upload_s3 = False

    result = await run_daily_backup(upload_s3=upload_s3)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
