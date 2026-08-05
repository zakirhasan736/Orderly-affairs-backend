from __future__ import annotations

from datetime import datetime
from enum import Enum


from app.config import settings
from app.notifications.mailer import send_email
from app.notifications.email_layout import (
    billing_url,
    email_cta_row,
    escape,
    paper_body,
    portal_url,
    render_reminder_card,
)


class TrialEmailEvent(Enum):
    STARTED = "started"
    DAY_10 = "day_10"
    DAY_3 = "day_3"
    ENDED = "ended"


def _fmt_day(dt: datetime | None) -> str:
    if not dt:
        return "the end date"
    return dt.strftime("%b %-d") if False else dt.strftime("%b %d").replace(" 0", " ")


def _weekday(dt: datetime | None) -> str:
    if not dt:
        return "soon"
    return dt.strftime("%A")


def _trial_end(user: dict) -> datetime | None:
    raw = (user.get("billing") or {}).get("trial_end")
    if raw is None:
        return None
    if getattr(raw, "tzinfo", None) is not None:
        return raw.replace(tzinfo=None)
    return raw


async def send_trial_email(*, user: dict, event: TrialEmailEvent):
    has_card = bool((user.get("billing") or {}).get("payment_method_attached"))
    trial_end = _trial_end(user)
    end_label = _fmt_day(trial_end)
    weekday = _weekday(trial_end)
    bill = billing_url()
    plans = portal_url()

    subject_map = {
        TrialEmailEvent.DAY_10: "Your Orderly Affairs trial – 10 days remaining",
        TrialEmailEvent.DAY_3: "Your trial ends in 3 days",
        TrialEmailEvent.ENDED: "Your trial has ended",
    }

    if event == TrialEmailEvent.DAY_10:
        if has_card:
            body = (
                f"Your saved card stays ready. On {escape(end_label)} you’ll be charged "
                "$94.95 for the year, or $9.95 monthly if you switch — unless you cancel."
            )
            cta = email_cta_row((bill, "Review billing"), (plans, "Compare plans"))
        else:
            body = (
                f"Add a card before {escape(end_label)} and nothing changes — you’ll be "
                "charged $94.95 for the year, or $9.95 monthly if you prefer. No card yet "
                "means your kit becomes read-only when the trial ends. Nothing is deleted "
                "either way."
            )
            cta = email_cta_row((bill, "Add a card"), (plans, "Compare plans"))
        html = render_reminder_card(
            schedule_label="Daily 09:00 · trial ending in 10 days",
            title=f"Your trial ends {weekday}.",
            preheader="Your free trial ends in 10 days",
            body_html=paper_body(body) + cta,
        )
    elif event == TrialEmailEvent.DAY_3:
        if has_card:
            body = (
                f"Three days left. On {escape(end_label)} your card will be charged "
                "$94.95 yearly (or $9.95 monthly if that’s your plan). Make sure the "
                "card is still valid."
            )
            cta = email_cta_row((bill, "Review billing"), (plans, "Compare plans"))
        else:
            body = (
                f"Add a card before {escape(end_label)} and nothing changes — you’ll be "
                "charged $94.95 for the year, or $9.95 monthly if you prefer. No card yet "
                f"means your kit becomes read-only on {_weekday(_next_day(trial_end))}. "
                "Nothing is deleted either way."
            )
            cta = email_cta_row((bill, "Add a card"), (plans, "Compare plans"))
        html = render_reminder_card(
            schedule_label="Daily 09:00 · trial ending in 3 days",
            title=f"Your trial ends {weekday}.",
            preheader="Your free trial ends in 3 days",
            body_html=paper_body(body) + cta,
        )
    elif event == TrialEmailEvent.ENDED:
        if has_card:
            body = (
                "Your trial has ended. We’re attempting payment with your saved card. "
                "You’ll keep access once payment succeeds."
            )
            cta = email_cta_row((bill, "Check billing"))
        else:
            body = (
                "Your trial has ended and no card is on file. Your kit is read-only "
                "until you activate a paid plan. Nothing has been deleted."
            )
            cta = email_cta_row((bill, "Add a card"), (plans, "Compare plans"))
        html = render_reminder_card(
            schedule_label="Daily 09:00 · trial ended",
            title="Your trial has ended.",
            preheader="Your Orderly Affairs trial has ended",
            body_html=paper_body(body) + cta,
            warning=not has_card,
        )
    else:
        return

    if event not in subject_map:
        return
    send_email(
        to_emails=user["email"],
        subject=subject_map[event],
        html_content=html,
    )


def _next_day(dt: datetime | None) -> datetime | None:
    if not dt:
        return None
    from datetime import timedelta

    return dt + timedelta(days=1)
