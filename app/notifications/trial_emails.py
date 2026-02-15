from enum import Enum
from sendgrid.helpers.mail import Mail
from sendgrid import SendGridAPIClient
from app.config import settings

class TrialEmailEvent(Enum):
    STARTED = "started"
    DAY_10 = "day_10"
    DAY_3 = "day_3"
    ENDED = "ended"

async def send_trial_email(*, user: dict, event: TrialEmailEvent):
    subject_map = {
        TrialEmailEvent.DAY_10: "Your Orderly Affairs trial – 5 days remaining",
        TrialEmailEvent.DAY_3: "Your trial ends in 3 days",
        TrialEmailEvent.ENDED: "Your trial has ended",
    }

    body_map = {
        TrialEmailEvent.DAY_10: """
            <p>Your free trial will end in <b>5 days</b>.</p>
            <p>Your card will be charged automatically unless you cancel.</p>
        """,
        TrialEmailEvent.DAY_3: """
            <p>Your free trial ends in <b>3 days</b>.</p>
            <p>Please ensure your payment method is valid.</p>
        """,
        TrialEmailEvent.ENDED: """
            <p>Your trial has ended.</p>
            <p>We are attempting payment using your saved card.</p>
        """,
    }

    sg = SendGridAPIClient(settings.SENDGRID_API_KEY)

    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=user["email"],
        subject=subject_map[event],
        html_content=body_map[event],
    )

    sg.send(message)
