from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import owner_login_url, settings
from app.notifications.display_names import resolve_owner_display_name


async def send_owner_inactivity_check_email(*, owner: dict) -> None:
    owner_name = await resolve_owner_display_name(owner)
    login_url = owner_login_url()

    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
      <p>Hello {owner_name},</p>
      <p>
        We noticed you have not signed in to your <strong>Orderly Affairs Kit</strong>
        for more than 90 days.
      </p>
      <p>
        If you are well, please sign in within the next <strong>15 days</strong>
        so we know your kit should remain active.
      </p>
      <p>
        <a href="{login_url}"
           style="
             display: inline-block;
             padding: 10px 18px;
             background: #2563eb;
             color: #ffffff;
             text-decoration: none;
             border-radius: 6px;
             font-weight: bold;">
          Sign in to Orderly Affairs
        </a>
      </p>
      <p style="color: #666; font-size: 14px;">
        Signing in is your confirmation that you are okay. If we do not see a
        sign-in within 15 days, your trusted Next-of-Kin workflows may proceed
        as if you are no longer able to manage the kit.
      </p>
    </div>
    """

    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=owner["email"],
        subject="Orderly Affairs – Please confirm you are okay",
        html_content=html,
    )

    sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
    sg.send(message)
