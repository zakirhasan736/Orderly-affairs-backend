from enum import Enum

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import settings
from app.notifications.email_layout import portal_url, render_simple_email


class BillingEmailEvent(Enum):
    WELCOME = "welcome"
    PLAN_TRIAL = "plan_trial"
    PLAN_MONTHLY = "plan_monthly"
    PLAN_YEARLY = "plan_yearly"


async def send_billing_email(*, user: dict, event: BillingEmailEvent):
    name = user.get("full_name") or user.get("name")
    subject_map = {
        BillingEmailEvent.WELCOME: "Welcome to Orderly Affairs",
        BillingEmailEvent.PLAN_TRIAL: "Your Free Trial Has Started",
        BillingEmailEvent.PLAN_MONTHLY: "Your Monthly Subscription Is Active",
        BillingEmailEvent.PLAN_YEARLY: "Your Yearly Subscription Is Active",
    }

    body_map = {
        BillingEmailEvent.WELCOME: render_simple_email(
            title="Welcome to Orderly Affairs",
            greeting_name=name,
            paragraphs=[
                "Your account has been successfully created.",
                "You can start organizing your vault whenever you're ready.",
            ],
            cta_url=portal_url(),
            cta_label="Open your vault",
            preheader="Your Orderly Affairs account is ready",
        ),
        BillingEmailEvent.PLAN_TRIAL: render_simple_email(
            title="Your free trial has started",
            greeting_name=name,
            paragraphs=[
                "You've started a <b>15-day free trial</b>.",
                "No charge today. Billing starts automatically after the trial "
                "unless you cancel.",
            ],
            cta_url=portal_url(),
            cta_label="Go to dashboard",
            preheader="Your 15-day free trial has started",
        ),
        BillingEmailEvent.PLAN_MONTHLY: render_simple_email(
            title="Monthly subscription active",
            greeting_name=name,
            paragraphs=[
                "Your <b>monthly subscription</b> is active.",
                "You'll be charged automatically every month.",
            ],
            cta_url=portal_url(),
            cta_label="View account",
            preheader="Your monthly subscription is active",
        ),
        BillingEmailEvent.PLAN_YEARLY: render_simple_email(
            title="Yearly subscription active",
            greeting_name=name,
            paragraphs=[
                "Your <b>yearly subscription</b> is active.",
                "You'll be charged automatically once per year.",
            ],
            cta_url=portal_url(),
            cta_label="View account",
            preheader="Your yearly subscription is active",
        ),
    }

    sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=user["email"],
        subject=subject_map[event],
        html_content=body_map[event],
    )
    sg.send(message)

    try:
        from app.notifications.push_bridge import notify_web_push

        await notify_web_push(
            user,
            title=subject_map[event],
            body="Open Orderly Affairs to review your subscription.",
            tag=f"billing-{event.value}",
            urgency="normal",
        )
    except Exception as exc:
        print("⚠️ Billing web push failed:", exc)
