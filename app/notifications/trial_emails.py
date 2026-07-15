from enum import Enum

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import settings


class TrialEmailEvent(Enum):
    STARTED = "started"
    DAY_10 = "day_10"
    DAY_3 = "day_3"
    ENDED = "ended"


async def send_trial_email(*, user: dict, event: TrialEmailEvent):
    has_card = bool((user.get("billing") or {}).get("payment_method_attached"))

    subject_map = {
        TrialEmailEvent.DAY_10: "Your Orderly Affairs trial – 10 days remaining",
        TrialEmailEvent.DAY_3: "Your trial ends in 3 days",
        TrialEmailEvent.ENDED: "Your trial has ended",
    }

    if has_card:
        charge_note_10 = "<p>Your saved card will be charged automatically when the trial ends unless you cancel.</p>"
        charge_note_3 = "<p>Please ensure your payment method is valid.</p>"
        charge_note_ended = "<p>We are attempting payment using your saved card.</p>"
    else:
        charge_note_10 = "<p>Add a payment method before the trial ends to keep access without interruption.</p>"
        charge_note_3 = "<p>No card is on file yet. Add billing details soon or access will pause when the trial ends.</p>"
        charge_note_ended = "<p>No successful payment was completed. Access is paused until you activate a paid plan.</p>"

    body_map = {
        TrialEmailEvent.DAY_10: f"""
            <p>Your free trial will end in <b>10 days</b>.</p>
            {charge_note_10}
        """,
        TrialEmailEvent.DAY_3: f"""
            <p>Your free trial ends in <b>3 days</b>.</p>
            {charge_note_3}
        """,
        TrialEmailEvent.ENDED: f"""
            <p>Your trial has ended.</p>
            {charge_note_ended}
        """,
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
