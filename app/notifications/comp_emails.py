"""Emails for owner-granted complimentary (free) access periods."""

from enum import Enum

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import settings


class CompEmailEvent(Enum):
    GRANTED = "granted"
    REMINDER_30 = "reminder_30"
    REMINDER_7 = "reminder_7"
    REMINDER_1 = "reminder_1"
    ENDED = "ended"


def _ends_label(ends_at) -> str:
    if not ends_at:
        return "Lifetime"
    try:
        return ends_at.strftime("%Y-%m-%d")
    except Exception:
        return str(ends_at)


async def send_comp_email(*, user: dict, event: CompEmailEvent, ends_at=None) -> None:
    email = user["email"]
    end_txt = _ends_label(ends_at or (user.get("billing") or {}).get("comp", {}).get("ends_at"))

    subject_map = {
        CompEmailEvent.GRANTED: "Complimentary access to Orderly Affairs",
        CompEmailEvent.REMINDER_30: "Your complimentary access ends in about 30 days",
        CompEmailEvent.REMINDER_7: "Your complimentary access ends in 7 days",
        CompEmailEvent.REMINDER_1: "Your complimentary access ends tomorrow",
        CompEmailEvent.ENDED: "Your complimentary access has ended",
    }

    body_map = {
        CompEmailEvent.GRANTED: f"""
            <p>You've been granted complimentary access to Orderly Affairs.</p>
            <p><b>Access until:</b> {end_txt}</p>
            <p>No payment is required during this period.</p>
        """,
        CompEmailEvent.REMINDER_30: f"""
            <p>Your complimentary access ends on <b>{end_txt}</b> (about 30 days).</p>
            <p>Please add a payment method and choose a plan before then to avoid interruption.</p>
        """,
        CompEmailEvent.REMINDER_7: f"""
            <p>Your complimentary access ends on <b>{end_txt}</b> (7 days).</p>
            <p>Activate a paid plan to keep your vault available after that date.</p>
        """,
        CompEmailEvent.REMINDER_1: f"""
            <p>Your complimentary access ends tomorrow (<b>{end_txt}</b>).</p>
            <p>Without a paid plan, login will be paused after that date.</p>
        """,
        CompEmailEvent.ENDED: """
            <p>Your complimentary access period has ended.</p>
            <p>Access is paused until a paid plan is activated. Please check this inbox
            for billing messages from Orderly Affairs and update your plan to continue.</p>
        """,
    }

    if event not in subject_map:
        return

    sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=email,
        subject=subject_map[event],
        html_content=body_map[event],
    )
    sg.send(message)
