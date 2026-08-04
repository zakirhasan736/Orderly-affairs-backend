#!/usr/bin/env python3
"""Run the weekly security monitor once (manual / CI)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


async def main() -> int:
    from app.security.weekly_monitor import run_weekly_security_monitor

    result = await run_weekly_security_monitor()
    print(json.dumps({k: result[k] for k in result if k != "audit"}, indent=2))
    print("\nFull audit keys:", list((result.get("audit") or {}).keys()))
    return 1 if result.get("issue_count") else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
