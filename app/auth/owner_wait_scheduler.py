from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.auth.after_death_notify import process_after_death_clocks

scheduler = AsyncIOScheduler()


async def _run_owner_wait() -> None:
    try:
        result = await process_after_death_clocks()
        reminded = int(result.get("reminded") or 0)
        closed = int(result.get("closed") or 0)
        eligible = int(result.get("eligible_alerts") or 0)
        realerts = int(result.get("realerts") or 0)
        if reminded or closed or eligible or realerts:
            print(
                f"After-death clocks: {reminded} reminder(s), "
                f"{closed} protection complete, {eligible} admin alert(s), "
                f"{realerts} realert(s)"
            )
    except Exception as exc:
        print(f"⚠️ Owner certificate-wait scheduler failed: {exc}")


def start_owner_wait_scheduler() -> None:
    scheduler.add_job(
        _run_owner_wait,
        trigger="interval",
        minutes=15,
        id="owner-certificate-wait-job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    if not scheduler.running:
        scheduler.start()
