#!/usr/bin/env python3
"""Decrypt an .oa1b backup package to a .tar.gz for restore drills.

Usage:
  python scripts/decrypt_backup.py storage/backups/orderly-backup-….oa1b
  python scripts/decrypt_backup.py path/to/file.oa1b -o /tmp/restore.tar.gz
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Decrypt Orderly Affairs .oa1b backup")
    parser.add_argument("package", type=Path, help="Path to .oa1b file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .tar.gz path (default: alongside package)",
    )
    args = parser.parse_args()

    from app.backup.crypto_file import decrypt_file

    src = args.package
    if not src.exists():
        print(f"Not found: {src}", file=sys.stderr)
        return 1
    dest = args.output or src.with_suffix(".tar.gz")
    decrypt_file(src, dest)
    print(f"Decrypted -> {dest}")
    print("Extract with: tar -xzf", dest)
    print(
        "Mongo restore: import each mongo/*.ndjson with mongoimport "
        "(documents remain ciphertext; keep AES_256_KEY)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
