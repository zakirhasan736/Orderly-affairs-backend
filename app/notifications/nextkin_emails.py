from datetime import datetime
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from app.config import nextkin_login_url, settings
from app.notifications.display_names import (
    resolve_nextkin_display_name,
    resolve_owner_display_name,
)


class NextKinEmailEvent:
    CREATED = "created"
    ACCESS_APPROVED = "access_approved"
    ACCESS_REVOKED = "access_revoked"
    PASSWORD_UPDATED = "password_updated"
    DELETED = "deleted"
    OWNER_DECEASED = "owner_deceased"


async def send_nextkin_email(
    *,
    event: str,
    nextkin: dict,
    owner: dict,
    plain_password: str | None = None,
):
    sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)

    subject = ""
    html = ""

    owner_name = await resolve_owner_display_name(owner)
    nk_name = resolve_nextkin_display_name(nextkin)

    if event == NextKinEmailEvent.CREATED:
        subject = "Orderly Affairs – You’ve been designated as Next-of-Kin"

        html = f"""
        <p>Hello {nk_name},</p>

        <p>{owner_name} has designated you as their <strong>Next-of-Kin</strong> within their Orderly Affairs account.</p>

        <p>This means that, at the appropriate time and once access is approved, you may be provided with access to important information they have prepared.</p>

        <p>{owner_name} may also send you a separate letter or email containing instructions about the location of their <strong>Password Card</strong> and the procedures required to access their records.</p>

        <p>Please keep any such instructions in a safe place.</p>

        <p>If you believe you received this message in error, you may disregard it.</p>

        <p>Kind regards,<br>
        The Orderly Affairs Team</p>
        """

    elif event == NextKinEmailEvent.ACCESS_APPROVED:
        subject = "Orderly Affairs – Access Granted"
        html = f"""
        <p>Hello {nk_name},</p>
        <p>{owner_name} has granted you <b>Immediate Access</b>.</p>

        <p><b>Login details:</b></p>
        <ul>
          <li>Email: {nextkin["email"]}</li>
          {f"<li>Password: {plain_password}</li>" if plain_password else ""}
        </ul>

        <p>
          <a href="{nextkin_login_url()}">
            Log in to Orderly Affairs
          </a>
        </p>
        """

    elif event == NextKinEmailEvent.ACCESS_REVOKED:
        subject = "Orderly Affairs – Access Revoked"
        html = f"""
        <p>Hello {nk_name},</p>
        <p>{owner_name} has revoked your access.</p>
        """

    elif event == NextKinEmailEvent.PASSWORD_UPDATED:
        subject = "Orderly Affairs – Your Login Password Was Updated"
        html = f"""
        <p>Hello {nk_name},</p>
        <p>{owner_name} has updated your <strong>Orderly Affairs</strong> login password.</p>

        <p><b>Updated login details:</b></p>
        <ul>
          <li>Email: {nextkin["email"]}</li>
          {f"<li>Password: {plain_password}</li>" if plain_password else ""}
        </ul>

        <p>
          <a href="{nextkin_login_url()}"
             style="
               display: inline-block;
               padding: 10px 18px;
               background: #2563eb;
               color: #ffffff;
               text-decoration: none;
               border-radius: 6px;
               font-weight: bold;">
            Log in to Orderly Affairs
          </a>
        </p>

        <p style="color: #666; font-size: 14px;">
          If you did not expect this change, please contact {owner_name} directly.
        </p>
        """

    elif event == NextKinEmailEvent.DELETED:
        subject = "Orderly Affairs – Next-of-Kin Removed"
        html = f"""
        <p>Hello {nk_name},</p>
        <p>Your Next-of-Kin designation under {owner_name} has been removed.</p>
        """

    elif event == NextKinEmailEvent.OWNER_DECEASED:
        subject = "Orderly Affairs – Access Available"
        html = f"""
        <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
          <p>Hello {nk_name},</p>
          <p>
            <strong>{owner_name}</strong> has passed away. You may now access
            their <strong>Orderly Affairs Kit</strong>.
          </p>
          <p><strong>Login details:</strong></p>
          <ul>
            <li>Email: {nextkin["email"]}</li>
            <li>Password: Use the password printed on your Password Card</li>
          </ul>
          <p>
            <a href="{nextkin_login_url()}"
               style="
                 display: inline-block;
                 padding: 10px 18px;
                 background: #2563eb;
                 color: #ffffff;
                 text-decoration: none;
                 border-radius: 6px;
                 font-weight: bold;">
              Log in to Orderly Affairs
            </a>
          </p>
          <p style="color: #666; font-size: 14px;">
            If you also received a Letter to Next of Kin, please follow any
            additional instructions it contains.
          </p>
        </div>
        """

    else:
        return  # unknown event → do nothing safely

    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=nextkin["email"],
        subject=subject,
        html_content=html,
    )

    try:
        sg.send(message)
    except Exception as e:
        print(f"⚠️ NextKin email failed ({event}):", e)


async def send_message_email(*, to: str, subject: str, html: str):
    """Legacy helper — prefer send_personal_message_email for personal messages."""
    sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)

    message = Mail(
        from_email=settings.MESSAGES_FROM_EMAIL,
        to_emails=to,
        subject=subject,
        html_content=html,
    )

    try:
        sg.send(message)
    except Exception as e:
        print("⚠️ Message delivery email failed:", e)
        raise
