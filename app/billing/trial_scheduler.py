from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.billing.access import complimentary_expired, get_comp, is_complimentary_active
from app.database import users_collection
from app.notifications.comp_emails import CompEmailEvent, send_comp_email
from app.notifications.payment_lock_emails import send_payment_lock_email
from app.notifications.trial_emails import TrialEmailEvent, send_trial_email

COMP_REMINDER_RULES = (
    (30, "30d", CompEmailEvent.REMINDER_30),
    (7, "7d", CompEmailEvent.REMINDER_7),
    (1, "1d", CompEmailEvent.REMINDER_1),
)

PAYMENT_FAIL_REMINDER_DAYS = (0, 3, 7)


async def process_trial_reminders():
    now = datetime.utcnow()

    cursor = users_collection.find({
        "billing.is_trial": True,
        "billing.status": "trialing",
        "billing.trial_end": {"$ne": None},
    })

    async for user in cursor:
        trial_end = user["billing"]["trial_end"]
        if getattr(trial_end, "tzinfo", None) is not None:
            trial_end = trial_end.replace(tzinfo=None)

        days_left = (trial_end - now).days
        has_card = bool(user["billing"].get("payment_method_attached"))
        auto_renew = user["billing"].get("auto_renew", True)

        if days_left == 10:
            await send_trial_email(user=user, event=TrialEmailEvent.DAY_10)
        elif days_left == 3:
            await send_trial_email(user=user, event=TrialEmailEvent.DAY_3)
        elif days_left <= 0:
            await send_trial_email(user=user, event=TrialEmailEvent.ENDED)

            if not has_card or not auto_renew:
                await users_collection.update_one(
                    {"_id": user["_id"]},
                    {
                        "$set": {
                            "billing.status": "blocked",
                            "billing.is_trial": False,
                            "billing.lock_reason": (
                                "trial_ended_no_card"
                                if not has_card
                                else "trial_ended_auto_renew_off"
                            ),
                            "billing.locked_at": now,
                            "updated_at": now,
                        }
                    },
                )
                try:
                    await send_payment_lock_email(
                        user=user, reason="trial_ended_unpaid"
                    )
                except Exception as exc:
                    print(f"payment lock email failed for {user.get('email')}: {exc}")


async def process_payment_fail_reminders():
    now = datetime.utcnow()
    cursor = users_collection.find({
        "role": "owner",
        "billing.status": {"$in": ["past_due", "blocked", "unpaid"]},
    })

    async for user in cursor:
        billing = user.get("billing") or {}
        locked_at = billing.get("locked_at")
        if locked_at is None:
            continue
        if getattr(locked_at, "tzinfo", None) is not None:
            locked_at = locked_at.replace(tzinfo=None)

        days_since = (now - locked_at).days
        sent = set(billing.get("payment_fail_reminders_sent") or [])

        for day in PAYMENT_FAIL_REMINDER_DAYS:
            key = f"d{day}"
            if days_since >= day and key not in sent:
                try:
                    await send_payment_lock_email(
                        user=user,
                        reason="payment_failed" if day > 0 else "trial_ended_unpaid",
                    )
                    await users_collection.update_one(
                        {"_id": user["_id"]},
                        {
                            "$addToSet": {"billing.payment_fail_reminders_sent": key},
                            "$set": {"updated_at": now},
                        },
                    )
                except Exception as exc:
                    print(f"payment fail reminder failed for {user.get('email')}: {exc}")


async def process_complimentary_access():
    now = datetime.utcnow()

    cursor = users_collection.find({
        "billing.comp.enabled": True,
        "role": "owner",
    })

    async for user in cursor:
        billing = user.get("billing") or {}
        comp = get_comp(billing)

        if comp["kind"] == "lifetime":
            continue

        ends_at = comp["ends_at"]
        if ends_at is None:
            continue

        if complimentary_expired(billing, now=now):
            if billing.get("status") != "blocked" or comp["enabled"]:
                await users_collection.update_one(
                    {"_id": user["_id"]},
                    {
                        "$set": {
                            "billing.status": "blocked",
                            "billing.comp.enabled": False,
                            "billing.lock_reason": "comp_ended",
                            "billing.locked_at": now,
                            "updated_at": now,
                        }
                    },
                )
                try:
                    await send_comp_email(
                        user=user, event=CompEmailEvent.ENDED, ends_at=ends_at
                    )
                except Exception as exc:
                    print(f"comp ended email failed for {user.get('email')}: {exc}")
            continue

        if not is_complimentary_active(billing, now=now):
            continue

        days_left = (ends_at - now).days
        reminders_sent = set(comp["reminders_sent"])

        for threshold, key, event in COMP_REMINDER_RULES:
            if days_left == threshold and key not in reminders_sent:
                try:
                    await send_comp_email(
                        user=user, event=event, ends_at=ends_at
                    )
                    await users_collection.update_one(
                        {"_id": user["_id"]},
                        {
                            "$addToSet": {"billing.comp.reminders_sent": key},
                            "$set": {"updated_at": now},
                        },
                    )
                except Exception as exc:
                    print(f"comp reminder {key} failed for {user.get('email')}: {exc}")


async def process_billing_schedulers():
    await process_trial_reminders()
    await process_complimentary_access()
    await process_payment_fail_reminders()


scheduler = AsyncIOScheduler()


def start_trial_scheduler():
    scheduler.add_job(
        process_billing_schedulers,
        trigger="cron",
        hour=9,
        minute=0,
        id="trial-reminder-job",
        replace_existing=True,
    )
    scheduler.start()
