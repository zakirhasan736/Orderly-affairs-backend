from __future__ import annotations

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import family_dashboard_login_url, nextkin_instructions_url, nextkin_login_url, settings
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
    ACCESS_REMINDER = "access_reminder"
    ACCESS_REVOKED = "access_revoked"
    PASSWORD_UPDATED = "password_updated"
    DELETED = "deleted"
    OWNER_DECEASED = "owner_deceased"
    IDENTITY_VERIFY = "identity_verify"


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
    access_timing: str = "immediate",
    portal_role_label: str | None = None,
    access_summary: str | None = None,
    instructions_url: str | None = None,
) -> str:
    """Paper/ink NOK invite — fluid max-width, brand logo on white tile."""
    from app.notifications.email_layout import email_brand_mark

    brand_mark = email_brand_mark()
    owner = escape(owner_name)
    hello = escape(_first_name(recipient_name))
    # This template is NOK-only. Never surface family portal roles (Viewer, …).
    role_line = escape("Next of Kin")
    access_line = escape(access_summary or "Sections granted by the kit owner")
    guide_url = instructions_url or nextkin_instructions_url()
    subject_line = f"{owner_name} has shared kit access with you"
    upon_death = str(access_timing or "").strip().lower() in {
        "upon_death",
        "upon-death",
        "death",
    }

    if upon_death:
        intro = (
            f"{owner} keeps an Orderly Affairs Kit — one place holding the accounts, "
            "documents, and wishes their family would need. They've named you as next of kin "
            "for when that kit is needed."
        )
        pwd_hint = (
            "You cannot sign in while they are living. After death is verified, "
            "you will get a one-time claim link and set your own password."
        )
        after_cta = (
            "SAVE THIS EMAIL. DO NOT DELETE IT. You will need the portal link later. "
            "Nothing is visible until death is verified and Orderly Affairs emails "
            "you a one-time claim link. You will set your own password then — nobody "
            "hands you theirs. If you weren't expecting this, you can ignore the email."
        )
        cta_label = "Sign in when ready"
    elif pending_approval:
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
        cta_label = "Sign in to the kit"
    else:
        intro = (
            f"{owner} keeps an Orderly Affairs Kit — one place holding the accounts, "
            "documents, and wishes their family would need. They've invited you to help "
            f"with a <strong>{role_line}</strong> role."
        )
        pwd_hint = (
            "Use it once with this email address. You'll set your own password on first sign-in."
        )
        after_cta = (
            f"Your access: <strong>{role_line}</strong> · {access_line}. "
            "Sign in when you're ready. If you weren't expecting this, you can ignore "
            "the email — no further access is granted by doing nothing."
        )
        cta_label = "Sign in to the kit"

    access_block = f"""
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:16px 0; border:1px solid #e4e6e1; border-radius:12px; overflow:hidden;">
                <tr>
                  <td style="padding:16px 20px; background:#f7f6f2;">
                    <p style="margin:0; font-family:{FONT_SANS}; font-size:12.5px; font-weight:500; color:#5c6b66;">Your access</p>
                    <p style="margin:8px 0 0 0; font-family:{FONT_SANS}; font-size:15px; font-weight:600; color:#132b26;">{role_line}</p>
                    <p style="margin:6px 0 0 0; font-family:{FONT_SANS}; font-size:13px; color:#6e7c77; line-height:1.5;">{access_line}</p>
                  </td>
                </tr>
              </table>
"""

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
    elif upon_death:
        password_block = f"""
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:22px 0; border:1px solid #e4e6e1; border-radius:12px; overflow:hidden;">
                <tr>
                  <td class="oa-pwd-pad" style="padding:20px; background:#f7f6f2;">
                    <p style="margin:0; font-family:{FONT_SANS}; font-size:12.5px; font-weight:500; color:#5c6b66;">Sign-in</p>
                    <p class="oa-pwd-hint" style="margin:10px 0 0 0; font-family:{FONT_SANS}; font-size:13px; color:#132b26; line-height:1.55;">
                      No password is stored for you yet. After death is verified, you will receive an email with a one-time claim link. You will set your own password. Until then, you cannot see inside the Vault.
                    </p>
                  </td>
                </tr>
              </table>
"""

    headline = f"{owner_name} shared kit access with you"
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
                    {brand_mark}
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

              {access_block}
              {password_block}

              <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0; width:100%;">
                <tr>
                  <td>
                    <a href="{escape(login_url)}" class="oa-cta" style="display:inline-block; padding:14px 22px; border-radius:24px; background:#132b26; color:#ffffff; font-family:{FONT_SANS}; font-size:14px; font-weight:500; text-decoration:none; line-height:1.2;">
                      {escape(cta_label)}
                    </a>
                  </td>
                </tr>
              </table>

              <p style="margin:22px 0 0 0; font-family:{FONT_SANS}; font-size:13.5px; line-height:1.7; color:#6e7c77;">
                {after_cta}
              </p>
              <p style="margin:14px 0 0 0; font-family:{FONT_SANS}; font-size:13.5px; line-height:1.7; color:#6e7c77;">
                Next-of-kin portal:{' '}
                <a href="{escape(login_url)}" style="color:#132b26; font-weight:500; text-decoration:underline; word-break:break-all;">
                  {escape(login_url)}
                </a>
              </p>
              <p style="margin:14px 0 0 0; font-family:{FONT_SANS}; font-size:13.5px; line-height:1.7; color:#6e7c77;">
                Bookmark{' '}
                <a href="{escape(guide_url)}" style="color:#132b26; font-weight:500; text-decoration:underline;">
                  Instructions for Your Next of Kin
                </a>
                — what to do now, and what happens when access is requested.
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
    claim_url: str | None = None,
    verify_url: str | None = None,
):
    sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)

    owner_name = await resolve_owner_display_name(owner)
    nk_name = resolve_nextkin_display_name(nextkin)
    login = nextkin_login_url()

    portal_role_label = "Next of Kin"
    access_level = nextkin.get("access_level") or "Full Kit Access"
    if access_level == "Full Kit Access":
        access_summary = "Full kit access"
    else:
        sections = nextkin.get("authorized_sections") or []
        access_summary = (
            f"Selected sections ({len(sections)})"
            if sections
            else "Selected sections"
        )

    if event == NextKinEmailEvent.CREATED:
        subject = f"{owner_name} has named you as next of kin"
        html = render_nok_invite_email(
            owner_name=owner_name,
            recipient_name=nk_name,
            plain_password=None,  # never email credentials for upon-death create
            login_url=login,
            pending_approval=False,
            access_timing="upon_death",
            portal_role_label=portal_role_label,
            access_summary=access_summary,
        )

    elif event == NextKinEmailEvent.ACCESS_APPROVED:
        subject = f"{owner_name} shared kit access with you"
        html = render_nok_invite_email(
            owner_name=owner_name,
            recipient_name=nk_name,
            plain_password=plain_password,
            login_url=login,
            pending_approval=False,
            access_timing="immediate",
            portal_role_label=portal_role_label,
            access_summary=access_summary,
        )

    elif event == NextKinEmailEvent.ACCESS_REMINDER:
        subject = f"Reminder: {owner_name} shared kit access with you"
        html = render_nok_invite_email(
            owner_name=owner_name,
            recipient_name=nk_name,
            plain_password=plain_password,
            login_url=login,
            pending_approval=False,
            access_timing="immediate",
            portal_role_label=portal_role_label,
            access_summary=access_summary,
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
        subject = f"{owner_name} updated your kit password"
        html = render_nok_invite_email(
            owner_name=owner_name,
            recipient_name=nk_name,
            plain_password=plain_password,
            login_url=login,
            pending_approval=False,
            access_timing=(
                "immediate"
                if nextkin.get("immediate_access")
                else "upon_death"
            ),
            portal_role_label=portal_role_label,
            access_summary=access_summary,
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
        claim = claim_url or login
        html = render_email(
            title="Kit access is now available",
            preheader=f"One-time link to open {owner_name}'s Orderly Affairs kit",
            body_html="".join(
                [
                    p(f"Hello {escape(nk_name)},"),
                    p(
                        f"<strong>{escape(owner_name)}</strong> has passed away. "
                        "Verification is complete. You may now open their "
                        "<strong>Orderly Affairs Kit</strong>."
                    ),
                    p(
                        "Use the one-time link below. It expires in 72 hours. "
                        "You will set your own password on first access, then "
                        "turn on two-factor authentication. Vault files are view "
                        "and download only — you cannot delete them. You can mark "
                        "tasks complete, add notes, deliver private messages, and "
                        "download documents."
                    ),
                    email_button(claim, "Open your one-time access link"),
                    p(
                        'Read <a href="'
                        + escape(nextkin_instructions_url())
                        + '" style="color:#132b26; font-weight:500;">Instructions for Your Next of Kin</a> '
                        "if you need a walkthrough of identity checks and claiming access."
                    ),
                    email_callout(
                        "If the link expires, contact Orderly Affairs support. "
                        "Do not share this link.",
                        tone="info",
                    ),
                ]
            ),
        )

    elif event == NextKinEmailEvent.IDENTITY_VERIFY:
        subject = "Orderly Affairs – Verify your identity"
        verify = verify_url or login
        html = render_email(
            title="Verify your identity to claim kit access",
            preheader="Government ID and a live selfie — the vault stays sealed until this clears",
            body_html="".join(
                [
                    p(f"Hello {escape(nk_name)},"),
                    p(
                        f"A passing was reported for <strong>{escape(owner_name)}</strong>. "
                        "Before anyone can open the kit, you must confirm you are the "
                        "person they named: a government-issued ID (national ID, "
                        "passport, or driver's license) plus a live selfie."
                    ),
                    p(
                        "This does not unlock the vault by itself. After your identity "
                        "clears, Orderly Affairs still reviews the case, then emails "
                        "you a one-time link to set your own password."
                    ),
                    email_button(verify, "Verify your identity"),
                    email_callout(
                        "Do not share this link. If this was unexpected, contact "
                        "support@orderly-affairs.com.",
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

    # Browser push for access-state changes (never for password / invite secrets).
    if event in (
        NextKinEmailEvent.ACCESS_APPROVED,
        NextKinEmailEvent.ACCESS_REMINDER,
        NextKinEmailEvent.ACCESS_REVOKED,
        NextKinEmailEvent.OWNER_DECEASED,
    ):
        try:
            from app.notifications.push_bridge import notify_web_push

            body_map = {
                NextKinEmailEvent.ACCESS_APPROVED: "You can open the shared kit now.",
                NextKinEmailEvent.ACCESS_REMINDER: "Your login details are still waiting.",
                NextKinEmailEvent.ACCESS_REVOKED: "Your kit access was revoked.",
                NextKinEmailEvent.OWNER_DECEASED: "Kit access is now available — sign in when ready.",
            }
            await notify_web_push(
                nextkin,
                title=subject,
                body=body_map.get(event, "Open Orderly Affairs for details."),
                tag=f"nok-{getattr(event, 'value', event)}",
                url=login,
                urgency=(
                    "high"
                    if event
                    in (
                        NextKinEmailEvent.ACCESS_REVOKED,
                        NextKinEmailEvent.OWNER_DECEASED,
                    )
                    else "normal"
                ),
            )
        except Exception as push_exc:
            print("⚠️ NextKin web push failed:", push_exc)


async def send_family_invite_email(
    *,
    family: dict,
    owner: dict,
    plain_password: str | None = None,
    password_only: bool = False,
):
    """Invite a family collaborator to the owner dashboard (separate session)."""
    sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
    owner_name = await resolve_owner_display_name(owner)
    recipient = resolve_nextkin_display_name(family)
    login = family_dashboard_login_url()

    from app.auth.portal_roles import role_label, resolve_dashboard_permissions

    role = role_label(family.get("portal_role"))
    perms = resolve_dashboard_permissions(family)
    access_level = family.get("access_level") or "Full Kit Access"
    if access_level == "Full Kit Access":
        areas = "Full owner dashboard (all vault sections + granted management areas)"
    else:
        sections = family.get("authorized_sections") or []
        areas = f"Selected dashboard areas ({len(sections)})"

    capability_bits = []
    if perms.get("can_upload"):
        capability_bits.append("document uploads")
    if perms.get("can_manage_family_access"):
        capability_bits.append("manage family access")
    if perms.get("can_manage_nextkin"):
        capability_bits.append("manage Next of Kin (Section 2)")
    if perms.get("can_manage_billing"):
        capability_bits.append("view billing")
    capabilities = (
        ", ".join(capability_bits) if capability_bits else "view granted areas only"
    )

    pwd_block = ""
    if plain_password:
        pwd_block = email_callout(
            f"<strong>Temporary password:</strong> "
            f"<code style='font-size:16px;letter-spacing:0.06em'>"
            f"{escape(plain_password)}</code>",
            tone="info",
        )

    if password_only:
        subject = f"{owner_name} updated your dashboard password"
        title = "Password updated"
        intro = (
            f"<b>{escape(owner_name)}</b> updated your family collaborator "
            "password for the owner dashboard."
        )
    else:
        subject = f"{owner_name} invited you to their Orderly Affairs dashboard"
        title = "Family dashboard access"
        intro = (
            f"<b>{escape(owner_name)}</b> invited you as a family collaborator "
            f"with the <b>{escape(role)}</b> role. This is a separate login from "
            "the owner — you must sign in with your own email and password. "
            "Signing in as the owner does not open your session."
        )

    html = render_simple_email(
        title=title,
        greeting_name=recipient,
        paragraphs=[
            intro,
            f"<b>Role:</b> {escape(role)} · <b>Access:</b> {escape(areas)}",
            f"<b>Capabilities:</b> {escape(capabilities)}",
            "Use the button below to open the family collaborator sign-in page "
            "and access the owner dashboard areas you were granted.",
        ],
        callout_html=pwd_block or None,
        cta_url=login,
        cta_label="Sign in to the dashboard",
        preheader="Your family collaborator dashboard invite",
    )

    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=family["email"],
        subject=subject,
        html_content=html,
    )
    try:
        sg.send(message)
    except Exception as e:
        print("Family invite email failed:", e)


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
