"""Emails when unpaid trial / failed payment locks an owner account."""


from app.config import settings
from app.notifications.mailer import send_email
from app.notifications.email_layout import (
    email_callout,
    portal_url,
    render_simple_email,
)


async def send_payment_lock_email(*, user: dict, reason: str = "trial_ended_unpaid") -> None:
    email = user["email"]
    name = user.get("full_name") or user.get("name")

    if reason == "trial_ended_unpaid":
        subject = "Action required: Orderly Affairs trial ended — access paused"
        html = render_simple_email(
            title="Access paused — billing required",
            greeting_name=name,
            paragraphs=[
                "Your free trial has ended and we could not activate a paid plan "
                "(for example, no card on file or payment failed).",
                "<b>Your login is paused</b> until billing is updated.",
            ],
            callout_html=email_callout(
                "Please complete payment when ready, or contact support if you "
                "believe this is a mistake.",
                tone="danger",
            ),
            cta_url=portal_url(),
            cta_label="Update billing",
            preheader="Your Orderly Affairs access is paused",
        )
    else:
        subject = "Action required: Orderly Affairs billing issue — access paused"
        html = render_simple_email(
            title="Billing issue — access paused",
            greeting_name=name,
            paragraphs=[
                "We could not process payment for your Orderly Affairs account.",
                "<b>Your login is paused</b> until the payment issue is resolved.",
            ],
            callout_html=email_callout(
                "Please check any billing emails from us and update your card or plan.",
                tone="warning",
            ),
            cta_url=portal_url(),
            cta_label="Resolve billing",
            preheader="Action required for your Orderly Affairs account",
        )
    send_email(
        to_emails=email,
        subject=subject,
        html_content=html,
    )
