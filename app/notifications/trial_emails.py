from enum import Enum

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import settings
from app.notifications.email_layout import (
    email_callout,
    p,
    portal_url,
    render_email,
    render_simple_email,
)


class TrialEmailEvent(Enum):
    STARTED = "started"
    DAY_10 = "day_10"
    DAY_3 = "day_3"
    ENDED = "ended"


async def send_trial_email(*, user: dict, event: TrialEmailEvent):
    has_card = bool((user.get("billing") or {}).get("payment_method_attached"))
    name = user.get("full_name") or user.get("name")

    subject_map = {
        TrialEmailEvent.DAY_10: "Your Orderly Affairs trial – 10 days remaining",
        TrialEmailEvent.DAY_3: "Your trial ends in 3 days",
        TrialEmailEvent.ENDED: "Your trial has ended",
    }

    if has_card:
        charge_note_10 = (
            "Your saved card will be charged automatically when the trial ends "
            "unless you cancel."
        )
        charge_note_3 = "Please ensure your payment method is valid."
        charge_note_ended = "We are attempting payment using your saved card."
        tone_ended = "info"
    else:
        charge_note_10 = (
            "Add a payment method before the trial ends to keep access without "
            "interruption."
        )
        charge_note_3 = (
            "No card is on file yet. Add billing details soon or access will pause "
            "when the trial ends."
        )
        charge_note_ended = (
            "No successful payment was completed. Access is paused until you "
            "activate a paid plan."
        )
        tone_ended = "warning"

    body_map = {
        TrialEmailEvent.DAY_10: render_simple_email(
            title="10 days left on your trial",
            greeting_name=name,
            paragraphs=[
                "Your free trial will end in <b>10 days</b>.",
                charge_note_10,
            ],
            cta_url=portal_url(),
            cta_label="Open Orderly Affairs",
            preheader="Your free trial ends in 10 days",
        ),
        TrialEmailEvent.DAY_3: render_simple_email(
            title="Trial ends in 3 days",
            greeting_name=name,
            paragraphs=[
                "Your free trial ends in <b>3 days</b>.",
                charge_note_3,
            ],
            callout_html=email_callout(
                "Review billing now so your vault stays available.",
                tone="warning",
            ),
            cta_url=portal_url(),
            cta_label="Manage billing",
            preheader="Your free trial ends in 3 days",
        ),
        TrialEmailEvent.ENDED: render_simple_email(
            title="Your trial has ended",
            greeting_name=name,
            paragraphs=["Your trial has ended.", charge_note_ended],
            callout_html=email_callout(
                "Complete billing to restore or continue access.",
                tone=tone_ended,
            ),
            cta_url=portal_url(),
            cta_label="Continue to portal",
            preheader="Your Orderly Affairs trial has ended",
        ),
    }

    if event not in subject_map:
        return

    sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=user["email"],
        subject=subject_map[event],
        html_content=body_map[event],
    )
    sg.send(message)
