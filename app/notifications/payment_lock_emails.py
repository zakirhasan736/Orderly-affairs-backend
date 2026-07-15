"""Emails when unpaid trial / failed payment locks an owner account."""

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import settings


async def send_payment_lock_email(*, user: dict, reason: str = "trial_ended_unpaid") -> None:
    email = user["email"]

    if reason == "trial_ended_unpaid":
        subject = "Action required: Orderly Affairs trial ended — access paused"
        html = """
            <p>Your free trial has ended and we could not activate a paid plan
            (for example, no card on file or payment failed).</p>
            <p><b>Your login is paused</b> until billing is updated.</p>
            <p>Please sign in when ready and complete payment, or reply to support
            if you believe this is a mistake.</p>
        """
    else:
        subject = "Action required: Orderly Affairs billing issue — access paused"
        html = """
            <p>We could not process payment for your Orderly Affairs account.</p>
            <p><b>Your login is paused</b> until the payment issue is resolved.</p>
            <p>Please check any billing emails from us and update your card / plan.</p>
        """

    sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=email,
        subject=subject,
        html_content=html,
    )
    sg.send(message)
