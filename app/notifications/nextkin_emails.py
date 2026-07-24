from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import nextkin_login_url, settings
from app.notifications.display_names import (
    resolve_nextkin_display_name,
    resolve_owner_display_name,
)
from app.notifications.email_layout import (
    email_button,
    email_callout,
    email_info_rows,
    escape,
    p,
    render_email,
    render_simple_email,
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

    owner_name = await resolve_owner_display_name(owner)
    nk_name = resolve_nextkin_display_name(nextkin)
    login = nextkin_login_url()

    if event == NextKinEmailEvent.CREATED:
        subject = "Orderly Affairs – You've been designated as Next-of-Kin"
        html = render_simple_email(
            title="You've been designated as Next-of-Kin",
            greeting_name=nk_name,
            paragraphs=[
                f"<b>{escape(owner_name)}</b> has designated you as their "
                f"<strong>Next-of-Kin</strong> within their Orderly Affairs account.",
                "At the appropriate time and once access is approved, you may be "
                "provided with access to important information they have prepared.",
                f"{escape(owner_name)} may also send a separate letter with "
                "instructions about their <strong>Password Card</strong> and how to "
                "access their records. Please keep any such instructions safe.",
            ],
            callout_html=email_callout(
                "If you believe you received this message in error, you may disregard it.",
                tone="info",
            ),
            preheader=f"{owner_name} designated you as Next-of-Kin",
        )

    elif event == NextKinEmailEvent.ACCESS_APPROVED:
        subject = "Orderly Affairs – Access Granted"
        details = [("Email", nextkin["email"])]
        if plain_password:
            details.append(("Password", plain_password))
        html = render_simple_email(
            title="Immediate access granted",
            greeting_name=nk_name,
            paragraphs=[
                f"<b>{escape(owner_name)}</b> has granted you "
                f"<b>Immediate Access</b> to their Orderly Affairs kit.",
            ],
            details=details,
            cta_url=login,
            cta_label="Log in to Orderly Affairs",
            preheader="Your Next-of-Kin access has been granted",
        )

    elif event == NextKinEmailEvent.ACCESS_REVOKED:
        subject = "Orderly Affairs – Access Revoked"
        html = render_simple_email(
            title="Access revoked",
            greeting_name=nk_name,
            paragraphs=[
                f"<b>{escape(owner_name)}</b> has revoked your Next-of-Kin access.",
            ],
            callout_html=email_callout(
                "If this was unexpected, please contact the kit owner directly.",
                tone="warning",
            ),
            preheader="Your Next-of-Kin access was revoked",
        )

    elif event == NextKinEmailEvent.PASSWORD_UPDATED:
        subject = "Orderly Affairs – Your Login Password Was Updated"
        details = [("Email", nextkin["email"])]
        if plain_password:
            details.append(("Password", plain_password))
        html = render_simple_email(
            title="Your login password was updated",
            greeting_name=nk_name,
            paragraphs=[
                f"<b>{escape(owner_name)}</b> has updated your Orderly Affairs "
                "login password.",
            ],
            details=details,
            cta_url=login,
            cta_label="Log in to Orderly Affairs",
            callout_html=email_callout(
                f"If you did not expect this change, please contact {escape(owner_name)} directly.",
                tone="info",
            ),
            preheader="Your Next-of-Kin password was updated",
        )

    elif event == NextKinEmailEvent.DELETED:
        subject = "Orderly Affairs – Next-of-Kin Removed"
        html = render_simple_email(
            title="Next-of-Kin designation removed",
            greeting_name=nk_name,
            paragraphs=[
                f"Your Next-of-Kin designation under <b>{escape(owner_name)}</b> "
                "has been removed.",
            ],
            preheader="Your Next-of-Kin designation was removed",
        )

    elif event == NextKinEmailEvent.OWNER_DECEASED:
        subject = "Orderly Affairs – Access Available"
        html = render_email(
            title="Kit access is now available",
            preheader=f"Access is available for {owner_name}'s Orderly Affairs kit",
            body_html="".join(
                [
                    p(f"Hello {escape(nk_name)},"),
                    p(
                        f"<strong>{escape(owner_name)}</strong> has passed away. "
                        "You may now access their <strong>Orderly Affairs Kit</strong>."
                    ),
                    email_info_rows(
                        [
                            ("Email", nextkin["email"]),
                            (
                                "Password",
                                "Use the password printed on your Password Card",
                            ),
                        ]
                    ),
                    email_button(login, "Log in to Orderly Affairs"),
                    email_callout(
                        "If you also received a Letter to Next of Kin, please follow "
                        "any additional instructions it contains.",
                        tone="info",
                    ),
                ]
            ),
        )

    else:
        return

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
