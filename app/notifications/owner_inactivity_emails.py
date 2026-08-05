
from app.config import owner_login_url, settings
from app.notifications.mailer import send_email
from app.notifications.display_names import resolve_owner_display_name
from app.notifications.email_layout import (
    email_callout,
    render_simple_email,
)


async def send_owner_inactivity_check_email(*, owner: dict) -> None:
    owner_name = await resolve_owner_display_name(owner)
    login_url = owner_login_url()

    html = render_simple_email(
        title="Please confirm you are okay",
        greeting_name=owner_name,
        paragraphs=[
            "We noticed you have not signed in to your "
            "<strong>Orderly Affairs Kit</strong> for more than 90 days.",
            "If you are well, please sign in within the next "
            "<strong>15 days</strong> so we know your kit should remain active.",
        ],
        callout_html=email_callout(
            "Signing in is your confirmation that you are okay. If we do not see "
            "a sign-in within 15 days, your trusted Next-of-Kin workflows may "
            "proceed as if you are no longer able to manage the kit.",
            tone="warning",
        ),
        cta_url=login_url,
        cta_label="Sign in to Orderly Affairs",
        preheader="Please confirm you are okay — sign in within 15 days",
    )

    send_email(
        to_emails=owner["email"],
        subject="Orderly Affairs – Please confirm you are okay",
        html_content=html,
    )
