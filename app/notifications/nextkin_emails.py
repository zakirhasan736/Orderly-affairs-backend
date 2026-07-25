from __future__ import annotations

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import nextkin_login_url, settings
from app.notifications.display_names import (
    resolve_nextkin_display_name,
    resolve_owner_display_name,
)
from app.notifications.email_layout import (
    escape,
    p,
    render_email,
    render_simple_email,
    email_button,
    email_callout,
    email_info_rows,
)

FONT_SANS = (
    "'Instrument Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',"
    "Helvetica,Arial,sans-serif"
)
FONT_SERIF = "'Instrument Serif',Georgia,'Times New Roman',serif"
FONT_MONO = "'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace"


class NextKinEmailEvent:
    CREATED = "created"
    ACCESS_APPROVED = "access_approved"
    ACCESS_REVOKED = "access_revoked"
    PASSWORD_UPDATED = "password_updated"
    DELETED = "deleted"
    OWNER_DECEASED = "owner_deceased"


def _first_name(full: str) -> str:
    parts = str(full or "").strip().split()
    return parts[0] if parts else "there"


def _support_line() -> str:
    phone = (getattr(settings, "SUPPORT_PHONE", None) or "").strip()
    support = getattr(settings, "EMAIL_SENDER", "support@orderly-affairs.com")
    if phone:
        return f"Questions? Reply to this email or call {escape(phone)}."
    return (
        f'Questions? Reply to this email or write '
        f'<a href="mailto:{escape(support)}" style="color:#132b26; text-decoration:none; '
        f'font-weight:500;">{escape(support)}</a>.'
    )


def render_nok_invite_email(
    *,
    owner_name: str,
    recipient_name: str,
    plain_password: str | None,
    login_url: str,
    pending_approval: bool,
) -> str:
    """Paper/ink NOK invite — fluid max-width, Cloudinary logo in ink mark."""
    from app.notifications.email_layout import brand_logo_url

    logo = escape(brand_logo_url())
    owner = escape(owner_name)
    hello = escape(_first_name(recipient_name))
    headline = f"{owner_name} has named you as their next of kin."
    subject_line = f"{owner_name} has named you as next of kin"

    if pending_approval:
        intro = (
            f"{owner} keeps an Orderly Affairs Kit — one place holding the accounts, "
            "documents, and wishes their family would need. They've given you a role in it."
        )
        pwd_hint = (
            "Use it once with this email address. You'll set your own password, then "
            f"{owner} approves your access."
        )
        after_cta = (
            f"Nothing is visible to you until {owner} approves your role. If you weren't "
            "expecting this, you can ignore the email — no access is granted by doing nothing."
        )
    else:
        intro = (
            f"{owner} keeps an Orderly Affairs Kit — one place holding the accounts, "
            "documents, and wishes their family would need. They've given you a role in it."
        )
        pwd_hint = (
            "Use it once with this email address. You'll set your own password on first sign-in."
        )
        after_cta = (
            "Sign in when you're ready. If you weren't expecting this, you can ignore "
            "the email — no further access is granted by doing nothing."
        )

    password_block = ""
    if plain_password:
        password_block = f"""
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:22px 0; border:1px solid #e4e6e1; border-radius:12px; overflow:hidden;">
                <tr>
                  <td class="oa-pwd-pad" style="padding:20px; background:#f7f6f2;">
                    <p style="margin:0; font-family:{FONT_SANS}; font-size:12.5px; font-weight:500; color:#5c6b66;">Your temporary password</p>
                    <p class="oa-pwd" style="margin:8px 0 0 0; font-family:{FONT_MONO}; font-size:22px; font-weight:500; letter-spacing:0.08em; color:#132b26; word-break:break-all;">{escape(plain_password)}</p>
                    <p class="oa-pwd-hint" style="margin:10px 0 0 0; font-family:{FONT_SANS}; font-size:12.5px; color:#6e7c77; line-height:1.55;">{pwd_hint}</p>
                  </td>
                </tr>
              </table>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{escape(subject_line)}</title>
  <style type="text/css">
    @media only screen and (max-width: 620px) {{
      .oa-shell {{ padding:14px 10px !important; }}
      .oa-pad {{ padding:20px 18px !important; }}
      .oa-header {{ padding:16px 18px !important; }}
      .oa-footer {{ padding:16px 18px !important; font-size:11.5px !important; }}
      .oa-title {{ font-size:22px !important; }}
      .oa-pwd {{ font-size:18px !important; letter-spacing:0.06em !important; }}
      .oa-cta {{
        display:block !important;
        width:100% !important;
        box-sizing:border-box !important;
        text-align:center !important;
        padding:15px !important;
        border-radius:26px !important;
        font-size:14.5px !important;
      }}
      .oa-logo-box {{ width:26px !important; height:26px !important; border-radius:7px !important; }}
      .oa-logo-img {{ width:18px !important; height:18px !important; }}
      .oa-brand {{ font-size:13px !important; }}
      .oa-pwd-pad {{ padding:16px !important; }}
      .oa-pwd-hint {{ display:none !important; }}
    }}
  </style>
</head>
<body style="margin:0; padding:0; background-color:#f2f1ec;">
  <div style="display:none; max-height:0; overflow:hidden; mso-hide:all;">
    {escape(subject_line)}
  </div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f2f1ec;">
    <tr>
      <td align="center" class="oa-shell" style="padding:24px 14px;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%; max-width:560px; background:#ffffff; border:1px solid #e4e6e1; border-radius:12px; overflow:hidden; font-size:15px; color:#132b26;">

          <tr>
            <td class="oa-header" style="padding:22px 32px; border-bottom:1px solid #f2f1ec;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td valign="middle" style="padding-right:11px;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" class="oa-logo-box" style="width:30px; height:30px; background:#132b26; border-radius:8px;">
                      <tr>
                        <td align="center" valign="middle" style="width:30px; height:30px;">
                          <img class="oa-logo-img" src="{logo}" width="22" height="22" alt="Orderly Affairs" style="display:block; width:22px; height:22px; border:0; outline:none; text-decoration:none;" />
                        </td>
                      </tr>
                    </table>
                  </td>
                  <td valign="middle" class="oa-brand" style="font-family:{FONT_SANS}; font-size:14px; font-weight:600; color:#132b26;">
                    Orderly Affairs
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td class="oa-pad" style="padding:32px;">
              <h1 class="oa-title" style="margin:0; font-family:{FONT_SERIF}; font-size:27px; font-weight:400; line-height:1.25; color:#132b26;">
                {escape(headline)}
              </h1>
              <p style="margin:16px 0 0 0; font-family:{FONT_SANS}; font-size:15px; line-height:1.7; color:#3c4a46;">
                Hello {hello},
              </p>
              <p style="margin:12px 0 0 0; font-family:{FONT_SANS}; font-size:15px; line-height:1.7; color:#3c4a46;">
                {intro}
              </p>

              {password_block}

              <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0; width:100%;">
                <tr>
                  <td>
                    <a href="{escape(login_url)}" class="oa-cta" style="display:inline-block; padding:14px 22px; border-radius:24px; background:#132b26; color:#ffffff; font-family:{FONT_SANS}; font-size:14px; font-weight:500; text-decoration:none; line-height:1.2;">
                      Sign in to the kit
                    </a>
                  </td>
                </tr>
              </table>

              <p style="margin:22px 0 0 0; font-family:{FONT_SANS}; font-size:13.5px; line-height:1.7; color:#6e7c77;">
                {after_cta}
              </p>
            </td>
          </tr>

          <tr>
            <td class="oa-footer" style="padding:20px 32px; border-top:1px solid #f2f1ec; background:#f7f6f2; font-family:{FONT_SANS}; font-size:12px; line-height:1.7; color:#8b9995;">
              Orderly Affairs · This message was sent because {owner} added your email to their kit.<br/>
              {_support_line()}
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


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
        subject = f"{owner_name} has named you as next of kin"
        html = render_nok_invite_email(
            owner_name=owner_name,
            recipient_name=nk_name,
            plain_password=plain_password,
            login_url=login,
            pending_approval=True,
        )

    elif event == NextKinEmailEvent.ACCESS_APPROVED:
        subject = f"{owner_name} has named you as next of kin"
        html = render_nok_invite_email(
            owner_name=owner_name,
            recipient_name=nk_name,
            plain_password=plain_password,
            login_url=login,
            pending_approval=False,
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
        html = render_nok_invite_email(
            owner_name=owner_name,
            recipient_name=nk_name,
            plain_password=plain_password,
            login_url=login,
            pending_approval=False,
        )
        # Prefer a clearer subject for password resets
        subject = f"{owner_name} updated your kit password"

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
        print(f"NextKin email failed ({event}):", e)


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
        print("Message delivery email failed:", e)
        raise
