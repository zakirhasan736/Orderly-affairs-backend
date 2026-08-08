"""Semi-annual “keep it current” kit review reminders."""

from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import settings
from app.database import users_collection
from app.notifications.email_layout import (
    email_chips,
    email_cta_row,
    kit_url,
    paper_body,
    render_reminder_card,
)

REVIEW_EVERY_DAYS = 180
scheduler = AsyncIOScheduler()


def build_kit_review_email() -> str:
    return render_reminder_card(
        schedule_label="Every 6 months · keep it current",
        title="Is this still true?",
        preheader="It’s been six months since you last reviewed your kit",
        body_html="".join(
            [
                paper_body(
                    "It’s been six months since you last reviewed your kit. "
                    "Three things change most often: bank accounts, insurance "
                    "policies, and who you’d want called first."
                ),
                email_chips(
                    [
                        "10-BANK Bank Accounts",
                        "5-INS Insurance Policies",
                        "1-START Key Contacts",
                    ]
                ),
                email_cta_row((kit_url(), "Review in 10 minutes")),
            ]
        ),
    )


async def send_kit_review_email(*, user: dict) -> None:
    email = user.get("email")
    if not email or not settings.SENDGRID_API_KEY:
        return
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=email,
        subject="Is this still true? — review your kit",
        html_content=build_kit_review_email(),
    )
    SendGridAPIClient(settings.SENDGRID_API_KEY).send(message)

    try:
        from app.notifications.push_bridge import notify_web_push

        await notify_web_push(
            user,
            title="Is this still true?",
            body="It’s time for a quick kit review — bank, insurance, and key contacts.",
            tag="kit-review",
            urgency="low",
        )
    except Exception as exc:
        print("⚠️ Kit review web push failed:", exc)


def _anchor_date(user: dict) -> datetime | None:
    billing = user.get("billing") or {}
    for key in (
        "kit_review_sent_at",
        "last_kit_review_at",
        "updated_at",
        "created_at",
        "billing.trial_start",
    ):
        if key == "billing.trial_start":
            raw = billing.get("trial_start") or billing.get("created_at")
        else:
            raw = user.get(key)
        if raw is None:
            continue
        if getattr(raw, "tzinfo", None) is not None:
            raw = raw.replace(tzinfo=None)
        if isinstance(raw, datetime):
            return raw
    return None


async def process_kit_review_reminders() -> None:
    now = datetime.utcnow()
    cursor = users_collection.find({"role": "owner"})
    async for user in cursor:
        sent_at = user.get("kit_review_sent_at")
        if sent_at is not None:
            if getattr(sent_at, "tzinfo", None) is not None:
                sent_at = sent_at.replace(tzinfo=None)
            if (now - sent_at).days < REVIEW_EVERY_DAYS:
                continue
        else:
            anchor = _anchor_date(user)
            if anchor is None or (now - anchor).days < REVIEW_EVERY_DAYS:
                continue

        try:
            await send_kit_review_email(user=user)
            await users_collection.update_one(
                {"_id": user["_id"]},
                {"$set": {"kit_review_sent_at": now, "updated_at": now}},
            )
        except Exception as exc:
            print(f"kit review email failed for {user.get('email')}: {exc}")


def start_kit_review_scheduler() -> None:
    # Align with design label “Every 6 months”; daily check at 09:15 UTC.
    scheduler.add_job(
        process_kit_review_reminders,
        trigger="cron",
        hour=9,
        minute=15,
        id="kit-review-reminder-job",
        replace_existing=True,
    )
    if not scheduler.running:
        scheduler.start()
