#!/usr/bin/env python3
"""SOC 2 Type I fail-closed checks. Local APP_ENV=development is allowed.

Exit 1 when APP_ENV is production/staging and a required control is missing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.config import settings  # noqa: E402
from app.security.malware_scan import ping_clamd  # noqa: E402


def _ok(name: str, passed: bool, detail: str = "") -> tuple[str, bool, str]:
    return name, passed, detail


def checks() -> list[tuple[str, bool, str]]:
    hardened = settings.is_hardened_runtime
    rows = [
        _ok(
            "APP_ENV production/staging when hardened",
            (not hardened) or settings.app_env_normalized in {"production", "prod", "staging"},
            f"APP_ENV={settings.APP_ENV!r}",
        ),
        _ok(
            "TURNSTILE_SECRET_KEY",
            bool(settings.TURNSTILE_SECRET_KEY) or not hardened,
            "required in production",
        ),
        _ok(
            "Admin owner-cookie fallback off in production",
            (not hardened) or (not settings.allow_owner_cookie_admin_fallback),
            f"allow_fallback={settings.allow_owner_cookie_admin_fallback}",
        ),
        _ok(
            "DOCUMENT_GUARD_SANITIZE",
            bool(settings.DOCUMENT_GUARD_SANITIZE),
            "",
        ),
        _ok(
            "ClamAV required in production",
            (not hardened) or settings.clamd_is_required,
            f"clamd_is_required={settings.clamd_is_required}",
        ),
        _ok(
            "VAULT_AUDIT_RETENTION_DAYS >= 365",
            int(getattr(settings, "VAULT_AUDIT_RETENTION_DAYS", 0) or 0) >= 365,
            str(getattr(settings, "VAULT_AUDIT_RETENTION_DAYS", "")),
        ),
        _ok(
            "AES_256_KEY present",
            bool(os.getenv("AES_256_KEY")),
            "",
        ),
    ]
    if settings.CLAMD_HOST or hardened:
        host = str(settings.CLAMD_HOST or "127.0.0.1").strip()
        try:
            alive = ping_clamd(host, int(settings.CLAMD_PORT or 3310), timeout=2)
        except OSError as exc:
            alive = False
            host = f"{host} ({exc})"
        rows.append(
            _ok(
                "clamd PONG",
                alive or not hardened,
                "required in production" if hardened else f"optional local ({host})",
            )
        )
    return rows


def main() -> int:
    print("\n=== SOC 2 Type I fail-closed ===\n")
    failed = 0
    for name, passed, detail in checks():
        mark = "PASS" if passed else "FAIL"
        if not passed:
            failed += 1
        extra = f" — {detail}" if detail else ""
        print(f"  {mark}  {name}{extra}")
    print()
    if settings.is_development:
        print("Runtime is development — production rows are informational.")
        print("On Hostinger set APP_ENV=production ADMIN_ALLOW_OWNER_COOKIE_FALLBACK=false")
        print("CLAMD_HOST=127.0.0.1 CLAMD_REQUIRED=true TURNSTILE_SECRET_KEY=<ssm>\n")
    return 1 if failed and settings.is_hardened_runtime else 0


if __name__ == "__main__":
    raise SystemExit(main())
