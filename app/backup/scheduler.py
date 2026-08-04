"""APScheduler job: daily encrypted user-data backup."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.backup.service import run_daily_backup
from app.config import settings

scheduler = AsyncIOScheduler()


async def _backup_job() -> None:
    try:
        result = await run_daily_backup()
        print(
            "Daily backup OK:",
            result.get("local_path"),
            f"sha256={result.get('sha256', '')[:16]}…",
            f"s3={result.get('s3_key')}",
        )
    except Exception as exc:
        print("Daily backup FAILED:", exc)


def start_backup_scheduler() -> None:
    if not settings.BACKUP_ENABLED:
        if settings.APP_ENV == "development":
            print("Daily backup scheduler disabled (BACKUP_ENABLED=false)")
        return

    scheduler.add_job(
        _backup_job,
        trigger="cron",
        hour=int(settings.BACKUP_CRON_HOUR),
        minute=int(settings.BACKUP_CRON_MINUTE),
        timezone="UTC",
        id="daily-encrypted-backup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    if not scheduler.running:
        scheduler.start()
    if settings.APP_ENV == "development":
        print(
            "Daily backup scheduler started "
            f"(cron {settings.BACKUP_CRON_HOUR:02d}:{settings.BACKUP_CRON_MINUTE:02d} UTC)"
        )
