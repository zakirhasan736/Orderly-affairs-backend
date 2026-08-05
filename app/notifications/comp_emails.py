"""Emails for owner-granted complimentary (free) access periods."""

from enum import Enum


from app.config import settings
from app.notifications.mailer import send_email
from app.notifications.email_layout import (
    email_callout,
    portal_url,
    render_simple_email,
)


class CompEmailEvent(Enum):
    GRANTED = "granted"
    REMINDER_30 = "reminder_30"
    REMINDER_7 = "reminder_7"
    REMINDER_1 = "reminder_1"
    ENDED = "ended"


def _ends_label(ends_at) -> str:
    if not ends_at:
        return "the end of your complimentary period"
    try:
        return ends_at.strftime("%Y-%m-%d")
    except Exception:
        return str(ends_at)


async def send_comp_email(*, user: dict, event: CompEmailEvent, ends_at=None) -> None:
    email = user["email"]
    name = user.get("full_name") or user.get("name")
    end_txt = _ends_label(
        ends_at or (user.get("billing") or {}).get("comp", {}).get("ends_at")
    )

    subject_map = {
        CompEmailEvent.GRANTED: "Complimentary access to Orderly Affairs",
        CompEmailEvent.REMINDER_30: "Your complimentary access ends in about 30 days",
        CompEmailEvent.REMINDER_7: "Your complimentary access ends in 7 days",
        CompEmailEvent.REMINDER_1: "Your complimentary access ends tomorrow",
        CompEmailEvent.ENDED: "Your complimentary access has ended",
    }

    body_map = {
        CompEmailEvent.GRANTED: render_simple_email(
            title="Complimentary access granted",
            greeting_name=name,
            paragraphs=[
                "You've been granted complimentary access to Orderly Affairs.",
                "No payment is required during this period.",
            ],
            details=[("Access until", end_txt)],
            cta_url=portal_url(),
            cta_label="Open your vault",
            preheader="Complimentary access to Orderly Affairs",
        ),
        CompEmailEvent.REMINDER_30: render_simple_email(
            title="Complimentary access ends in ~30 days",
            greeting_name=name,
            paragraphs=[
                f"Your complimentary access ends on <b>{end_txt}</b> (about 30 days).",
                "Please add a payment method and choose a plan before then to avoid "
                "interruption.",
            ],
            cta_url=portal_url(),
            cta_label="Add billing details",
            preheader="Complimentary access ends in about 30 days",
        ),
        CompEmailEvent.REMINDER_7: render_simple_email(
            title="Complimentary access ends in 7 days",
            greeting_name=name,
            paragraphs=[
                f"Your complimentary access ends on <b>{end_txt}</b> (7 days).",
                "Activate a paid plan to keep your vault available after that date.",
            ],
            callout_html=email_callout(
                "A paid plan is required after this date to keep access.",
                tone="warning",
            ),
            cta_url=portal_url(),
            cta_label="Choose a plan",
            preheader="Complimentary access ends in 7 days",
        ),
        CompEmailEvent.REMINDER_1: render_simple_email(
            title="Complimentary access ends tomorrow",
            greeting_name=name,
            paragraphs=[
                f"Your complimentary access ends tomorrow (<b>{end_txt}</b>).",
                "Without a paid plan, login will be paused after that date.",
            ],
            callout_html=email_callout(
                "Act today to avoid an interruption.",
                tone="danger",
            ),
            cta_url=portal_url(),
            cta_label="Activate a plan",
            preheader="Complimentary access ends tomorrow",
        ),
        CompEmailEvent.ENDED: render_simple_email(
            title="Complimentary access has ended",
            greeting_name=name,
            paragraphs=[
                "Your complimentary access period has ended.",
                "Access is paused until a paid plan is activated.",
            ],
            callout_html=email_callout(
                "Update your plan to continue using Orderly Affairs.",
                tone="warning",
            ),
            cta_url=portal_url(),
            cta_label="Update your plan",
            preheader="Your complimentary access has ended",
        ),
    }

    if event not in subject_map:
        return
    send_email(
        to_emails=email,
        subject=subject_map[event],
        html_content=body_map[event],
    )
