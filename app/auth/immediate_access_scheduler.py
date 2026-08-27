from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.auth.immediate_access_grant import (
    complete_due_immediate_access_grants,
    expire_unused_living_credentials,
    send_due_living_access_reminders,
)

scheduler = AsyncIOScheduler()


async def _run_due_grants() -> None:
    try:
        sent = await complete_due_immediate_access_grants()
        if sent:
            print(f"Sent {sent} delayed next-of-kin access email(s)")
        reminded = await send_due_living_access_reminders()
        if reminded:
            print(f"Sent {reminded} next-of-kin access reminder(s)")
        expired = await expire_unused_living_credentials()
        if expired:
            print(f"Expired {expired} unused next-of-kin login window(s)")
    except Exception as exc:
        print(f"⚠️ Immediate-access grant scheduler failed: {exc}")


def start_immediate_access_scheduler() -> None:
    scheduler.add_job(
        _run_due_grants,
        trigger="interval",
        seconds=30,
        id="immediate-access-grant-job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    if not scheduler.running:
        scheduler.start()
