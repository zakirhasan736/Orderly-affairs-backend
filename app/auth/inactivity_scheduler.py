from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.auth.death_detection import (
    OWNER_FOLLOWUP_DAYS,
    OWNER_INACTIVE_DAYS,
    _as_utc,
    _owner_last_activity,
)
from app.database import users_collection
from app.notifications.owner_inactivity_emails import send_owner_inactivity_check_email

scheduler = AsyncIOScheduler()


async def process_owner_inactivity() -> None:
    now = datetime.now(timezone.utc)
    inactive_cutoff = now - timedelta(days=OWNER_INACTIVE_DAYS)
    followup_cutoff = now - timedelta(days=OWNER_FOLLOWUP_DAYS)

    alive_owners = users_collection.find(
        {
            "role": "owner",
            "$or": [
                {"owner_status": {"$exists": False}},
                {"owner_status": {"$ne": "deceased"}},
            ],
        }
    )

    async for owner in alive_owners:
        owner_id = str(owner["_id"])
        last_activity = _as_utc(_owner_last_activity(owner))
        if not last_activity or last_activity > inactive_cutoff:
            continue

        warning_sent_at = _as_utc(owner.get("inactivity_warning_sent_at"))
        last_login = _as_utc(owner.get("last_login_at"))

        if not warning_sent_at:
            try:
                await send_owner_inactivity_check_email(owner=owner)
                await users_collection.update_one(
                    {"_id": owner["_id"]},
                    {
                        "$set": {
                            "inactivity_warning_sent_at": datetime.utcnow(),
                            "updated_at": datetime.utcnow(),
                        }
                    },
                )
                print(f"Sent inactivity check email to owner {owner_id}")
            except Exception as e:
                print(f"⚠️ Inactivity email failed for owner {owner_id}: {e}")
            continue

        if warning_sent_at > followup_cutoff:
            continue

        owner_replied = last_login and last_login > warning_sent_at
        if owner_replied:
            await users_collection.update_one(
                {"_id": owner["_id"]},
                {
                    "$unset": {"inactivity_warning_sent_at": ""},
                    "$set": {"updated_at": datetime.utcnow()},
                },
            )
            continue

        if owner.get("inactivity_escalated_at"):
            continue

        try:
            await send_owner_inactivity_check_email(owner=owner)
        except Exception as e:
            print(f"⚠️ Final inactivity escalation email failed for owner {owner_id}: {e}")

        await users_collection.update_one(
            {"_id": owner["_id"]},
            {
                "$set": {
                    "inactivity_escalated_at": datetime.utcnow(),
                    "inactivity_requires_manual_review": True,
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        print(
            f"Owner {owner_id} flagged for manual death review after "
            f"{OWNER_INACTIVE_DAYS}+{OWNER_FOLLOWUP_DAYS} days inactivity"
        )


def start_owner_inactivity_scheduler() -> None:
    scheduler.add_job(
        process_owner_inactivity,
        trigger="cron",
        hour=1,
        minute=30,
        id="owner-inactivity-job",
        replace_existing=True,
    )
    scheduler.start()
