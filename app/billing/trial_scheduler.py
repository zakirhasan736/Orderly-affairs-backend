from datetime import datetime
from app.database import users_collection
from app.notifications.trial_emails import send_trial_email, TrialEmailEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler

async def process_trial_reminders():
    now = datetime.utcnow()

    cursor = users_collection.find({
        "billing.is_trial": True,
        "billing.status": "trialing",
        "billing.trial_end": {"$ne": None},
    })

    async for user in cursor:
        trial_end = user["billing"]["trial_end"]
        days_left = (trial_end - now).days

        # 🔔 10 days left
        if days_left == 10:
            await send_trial_email(
                user=user,
                event=TrialEmailEvent.DAY_10
            )

        # 🔔 3 days left
        elif days_left == 3:
            await send_trial_email(
                user=user,
                event=TrialEmailEvent.DAY_3
            )

        # 🔔 Trial ended (Stripe will charge automatically)
        elif days_left == 0:
            await send_trial_email(
                user=user,
                event=TrialEmailEvent.ENDED
            )

scheduler = AsyncIOScheduler()
def start_trial_scheduler():
    scheduler.add_job(
        process_trial_reminders,
        trigger="cron",
        hour=0,      # once per day
        minute=0,    # midnight UTC
        id="trial-reminder-job",
        replace_existing=True,
    )
    scheduler.start()