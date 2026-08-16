#!/usr/bin/env python3
"""Backup restore *drill* — never restore onto the live orderly_affairs DB.

Writes a dated evidence note. Optionally decrypts the newest .oa1b package
to prove the backup key works. Import into a throwaway Mongo database
manually (see docs/BACKUP.md).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def newest_package(backup_root: Path) -> Path | None:
    files = sorted(backup_root.glob("*.oa1b"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def main() -> int:
    from app.config import settings

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    evidence_dir = ROOT / "docs" / "compliance" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    note = evidence_dir / f"backup-restore-{stamp}.md"

    backup_root = Path(getattr(settings, "BACKUP_ROOT", "storage/backups") or "storage/backups")
    if not backup_root.is_absolute():
        backup_root = ROOT / backup_root
    package = newest_package(backup_root)

    lines = [
        f"# Backup restore drill — {stamp}",
        "",
        f"- Operator: {os_user()}",
        f"- UTC: {datetime.now(timezone.utc).isoformat()}",
        f"- Live Mongo DB: orderly_affairs (**not** used)",
        f"- Throwaway DB name: orderly_affairs_restore_{stamp}",
        f"- Package: {package if package else 'NONE FOUND'}",
        "",
        "## Result",
        "",
    ]
    if not package:
        lines += [
            "INCOMPLETE — no `.oa1b` under BACKUP_ROOT. Run `python scripts/run_backup.py --no-s3` first.",
            "",
        ]
        note.write_text("\n".join(lines), encoding="utf-8")
        print(f"Wrote {note}")
        return 1

    try:
        from app.backup.crypto_file import decrypt_file

        out_tar = backup_root / f"drill-{stamp}.tar.gz"
        decrypt_file(package, out_tar)
        lines += [
            f"PASS — decrypted `{package.name}` → `{out_tar.name}`.",
            "Next: `tar -xzf` and mongoimport into the throwaway DB only.",
            "Then delete the throwaway DB. Do not point MONGO_URL at it.",
            "",
        ]
        note.write_text("\n".join(lines), encoding="utf-8")
        print(f"Wrote {note}")
        return 0
    except Exception as exc:
        import traceback

        lines += [
            f"FAIL — decrypt error: {type(exc).__name__}: {exc or '(no message)'}",
            "```",
            traceback.format_exc()[:2000],
            "```",
            "",
        ]
        note.write_text("\n".join(lines), encoding="utf-8")
        print(f"Wrote {note}")
        return 1


def os_user() -> str:
    import os

    return os.getenv("USERNAME") or os.getenv("USER") or "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
