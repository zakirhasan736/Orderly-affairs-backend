#!/usr/bin/env python3
"""Script 2 placeholder. Do not import until Script 1 export is verified."""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="vBizMe import (not enabled until export is verified)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--slug", default=None)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.parse_args()
    print(
        "IMPORT IS NOT ENABLED YET.\n"
        "Run and inspect Script 1 first:\n"
        "  python migration/export_vbizme.py --discover-only\n"
        "  python migration/export_vbizme.py --slug kenneth-rivera\n"
        "  python migration/export_vbizme.py --verify\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
