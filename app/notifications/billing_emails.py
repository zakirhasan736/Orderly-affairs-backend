from enum import Enum
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from app.config import settings

class BillingEmailEvent(Enum):
    WELCOME = "welcome"
    PLAN_TRIAL = "plan_trial"
    PLAN_MONTHLY = "plan_monthly"
    PLAN_YEARLY = "plan_yearly"

async def send_billing_email(*, user: dict, event: BillingEmailEvent):
    subject_map = {
        BillingEmailEvent.WELCOME: "Welcome to Orderly Affairs",
        BillingEmailEvent.PLAN_TRIAL: "Your Free Trial Has Started",
        BillingEmailEvent.PLAN_MONTHLY: "Your Monthly Subscription Is Active",
        BillingEmailEvent.PLAN_YEARLY: "Your Yearly Subscription Is Active",
    }

    body_map = {
        BillingEmailEvent.WELCOME: """
            <h2>Welcome to Orderly Affairs</h2>
            <p>Your account has been successfully created.</p>
        """,

        BillingEmailEvent.PLAN_TRIAL: """
            <p>You’ve started a <b>15-day free trial</b>.</p>
            <p>No charge today. Billing starts automatically after the trial.</p>
        """,

        BillingEmailEvent.PLAN_MONTHLY: """
            <p>Your <b>monthly subscription</b> is active.</p>
            <p>You’ll be charged automatically every month.</p>
        """,

        BillingEmailEvent.PLAN_YEARLY: """
            <p>Your <b>yearly subscription</b> is active.</p>
            <p>You’ll be charged automatically once per year.</p>
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
