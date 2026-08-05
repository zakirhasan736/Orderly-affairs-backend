# app/letters/email_utils.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict


from app.config import nextkin_login_url, settings
from app.notifications.mailer import send_email as ses_send_email

FONT_SANS = (
    "'Instrument Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',"
    "Helvetica,Arial,sans-serif"
)
FONT_SERIF = "'Instrument Serif',Georgia,'Times New Roman',serif"
FONT_MONO = "'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace"

DOCUMENTS_BAG_DEFAULT = (
    "• The Documents Bag: Please keep this safe. It contains originals of the essential documents "
    "that you may need to refer to it even after everything has been settled. It is located"
)
LEGACY_DOCUMENTS_BAG_INFO = (
    "• The Documents Bag: Please keep this safe. It contains original documents and space to store "
    "items such as death certificates. You may need to refer to it even after everything has been settled. It is located"
)


def _documents_bag_info(doc: Dict[str, Any]) -> str:
    value = str(doc.get("documents_bag_info") or "").strip()
    if not value or value == LEGACY_DOCUMENTS_BAG_INFO:
        return DOCUMENTS_BAG_DEFAULT
    return value


def render_letter_text(doc: Dict[str, Any]) -> str:
    def fmt_date(v):
        if not v:
            return "Upon Death"
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            return d.strftime("%B %d, %Y")
        except Exception:
            return str(v)

    # Build the long default line separately to avoid f-string bracket pitfalls
    default_login_line = (
        f"I have registered your email address ({doc.get('nok_email') or '[Email will auto-populate]'}) "
        f"and your phone number ({doc.get('nok_phone') or '[Phone will auto-populate]'})"
        f", which you can use as your login credentials. "
        f"The password to gain access to the kit, is printed on a password card located "
        f"{doc.get('password_card_location') or '[Password Card Location will auto-populate]'}."
    )

    signer = str(doc.get("signer_name") or doc.get("owner_name") or "").strip() or "[Your name]"

    return f"""{fmt_date(doc.get("letter_date"))}

{doc.get("letter_greeting") or "Dear"} {doc.get("letter_to") or "[Next of Kin Name]"},


{doc.get("letter_opening") or "I'm writing you this note as someone I trust deeply.\n\nAs my next of kin, the executor of my will, a close friend, my attorney, or someone who cares—I want you to know that I've prepared something to help guide you through what comes next."}

{doc.get("kit_description") or "I've subscribed to an Orderly Affairs Kit. Inside, you'll find everything you may need to manage my affairs if I'm no longer able to, or when I'm gone. It includes not only documents, but also instructions—gentle step-by-step guides to make this process less overwhelming."}

You can access the kit online at: {doc.get("access_url") or nextkin_login_url()}

{doc.get("login_credentials_text") or default_login_line}

{doc.get("accessible_sections") or "Once you log in, you'll be able to manage the sections below on my behalf:\n\n(Autofill sections based on selection in the access management section)"}

In addition to the online kit, you'll find two important physical items:

{doc.get("key_bag_info") or "• The Key Bag: This contains important keys and a guide to what each is for. It may include house keys, PO box keys, or vehicle keys. It is located"} {doc.get("key_bag_location") or "[Key Bag Location]"}.

{_documents_bag_info(doc)} {doc.get("documents_bag_location") or "[Documents Bag Location]"}.

{doc.get("incomplete_kit_message") or "If any part of the kit is incomplete, please don't worry. Even the unfinished parts can still help you stay organized. I've done my best to make sure you won't be left searching through drawers or wondering where things are."}

{doc.get("closing_message") or "Above all, this kit is my way of caring for you—even when I can't be here in person.\n\nTake your time. Breathe. You've got this, and I'm grateful it's you."}

{doc.get("letter_signature") or "With love,"}

{signer}
"""


def _escape(value: object) -> str:
    import html

    return html.escape("" if value is None else str(value), quote=True)


def _is_placeholder(text: str | None) -> bool:
    if not text or not str(text).strip():
        return True
    t = str(text).strip().lower()
    return t.startswith("[") and t.endswith("]")


def _paragraphs_html(text: str) -> str:
    """Split plain text into spaced paragraphs."""
    chunks = [c.strip() for c in str(text).replace("\r\n", "\n").split("\n\n") if c.strip()]
    if not chunks:
        # single newlines as soft breaks inside one block
        lines = [ln.strip() for ln in str(text).split("\n") if ln.strip()]
        if not lines:
            return ""
        return (
            f'<p style="margin:0 0 16px 0; font-family:{FONT_SANS}; font-size:15.5px; '
            f'line-height:1.85; color:#3c4a46;">'
            + "<br/>".join(_escape(ln) for ln in lines)
            + "</p>"
        )
    parts = []
    for i, chunk in enumerate(chunks):
        margin = "0 0 16px 0" if i < len(chunks) - 1 else "0"
        inner = _escape(chunk).replace("\n", "<br/>")
        parts.append(
            f'<p style="margin:{margin}; font-family:{FONT_SANS}; font-size:15.5px; '
            f'line-height:1.85; color:#3c4a46;">{inner}</p>'
        )
    return "".join(parts)


def _support_footer_line() -> str:
    phone = (getattr(settings, "SUPPORT_PHONE", None) or "").strip()
    support = getattr(settings, "EMAIL_SENDER", "support@orderly-affairs.com")
    if phone:
        return (
            f"If you need help, call {_escape(phone)} — a person answers, "
            "weekdays 8am–8pm."
        )
    return (
        f'If you need help, contact '
        f'<a href="mailto:{_escape(support)}" style="color:#132b26; text-decoration:none; '
        f'font-weight:500;">{_escape(support)}</a>.'
    )


def render_email_html(doc: Dict[str, Any], *, owner_name: str | None = None) -> str:
    """NOK death / scheduled letter — paper/ink design (fluid max-width)."""
    greeting = (doc.get("letter_greeting") or "Dear").strip()
    to_name = (doc.get("letter_to") or "there").strip()
    if not to_name or _is_placeholder(to_name):
        to_name = "there"
    headline = f"{greeting} {to_name},"

    body_parts: list[str] = []
    opening = doc.get("letter_opening")
    if opening and not _is_placeholder(opening):
        body_parts.append(_paragraphs_html(str(opening)))
    else:
        body_parts.append(
            _paragraphs_html(
                "If you're reading this, then I've passed, and the job of sorting "
                "things out has fallen to you. I'm sorry. I also know you'll do it well."
            )
        )

    kit_desc = doc.get("kit_description")
    if kit_desc and not _is_placeholder(kit_desc):
        body_parts.append(_paragraphs_html(str(kit_desc)))

    incomplete = doc.get("incomplete_kit_message")
    if incomplete and not _is_placeholder(incomplete):
        body_parts.append(_paragraphs_html(str(incomplete)))

    closing = doc.get("closing_message")
    signature = (doc.get("letter_signature") or "With all my love,").strip()
    if closing and not _is_placeholder(closing):
        close_text = str(closing).strip()
        # Avoid duplicating a sign-off if closing already ends with the signature tone
        if signature.lower() not in close_text.lower():
            close_text = f"{close_text}\n{signature}"
    else:
        close_text = signature
    body_parts.append(
        f'<p class="oa-sign" style="margin:0; font-family:{FONT_SERIF}; font-size:22px; '
        f'font-weight:400; font-style:italic; line-height:1.35; color:#132b26;">'
        f'{_escape(close_text).replace(chr(10), "<br/>")}</p>'
    )

    # Always send NOK recipients to the Next-of-Kin login (not marketing site).
    kit_url = nextkin_login_url()
    owner_label = (owner_name or doc.get("owner_name") or "").strip()
    signer_label = (
        (doc.get("signer_name") or owner_label or "").strip()
    )
    if signer_label and not _is_placeholder(signer_label):
        body_parts.append(
            f'<p style="margin:10px 0 0 0; font-family:{FONT_SANS}; font-size:15px; '
            f'font-weight:600; line-height:1.4; color:#213D59;">'
            f'{_escape(signer_label)}</p>'
        )
    if owner_label:
        first = owner_label.split()[0]
        cta_label = f"Open {first}'s kit"
        footer_owner = f"Sent on {_escape(owner_label)}'s instructions."
    else:
        cta_label = "Open the kit"
        footer_owner = "Sent on your loved one's instructions."

    card_loc = doc.get("password_card_location")
    if card_loc and not _is_placeholder(card_loc):
        hint = (
            f"You'll need the code from the password card. It's kept in "
            f"{_escape(card_loc)}. Take your time — nothing here expires."
        )
    else:
        hint = (
            "You'll need the code from the password card. Take your time — "
            "nothing here expires."
        )

    from app.notifications.email_layout import email_brand_mark

    brand_mark = email_brand_mark()
    support_line = _support_footer_line()

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>A letter from your loved one</title>
  <style type="text/css">
    @media only screen and (max-width: 620px) {{
      .oa-shell {{ padding:14px 10px !important; }}
      .oa-pad {{ padding:22px 18px !important; }}
      .oa-header {{ padding:16px 18px !important; }}
      .oa-footer {{ padding:16px 18px !important; font-size:11.5px !important; }}
      .oa-kicker {{ font-size:9.5px !important; }}
      .oa-title {{ font-size:24px !important; margin-top:14px !important; }}
      .oa-body p {{ font-size:14.5px !important; line-height:1.8 !important; }}
      .oa-sign {{ font-size:20px !important; }}
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
      .oa-cta-rule {{ border-top:none !important; padding-top:0 !important; margin-top:20px !important; }}
    }}
  </style>
</head>
<body style="margin:0; padding:0; background-color:#f2f1ec;">
  <div style="display:none; max-height:0; overflow:hidden; mso-hide:all;">
    A letter from your loved one, prepared through Orderly Affairs.
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
            <td class="oa-pad" style="padding:36px 32px;">
              <p class="oa-kicker" style="margin:0; font-family:{FONT_MONO}; font-size:10px; font-weight:500; letter-spacing:0.14em; text-transform:uppercase; color:#a5b1ad;">
                A letter from your loved one
              </p>
              <h1 class="oa-title" style="margin:20px 0 0 0; font-family:{FONT_SERIF}; font-size:30px; font-weight:400; line-height:1.2; color:#132b26;">
                {_escape(headline)}
              </h1>

              <div class="oa-body" style="margin-top:18px;">
                {''.join(body_parts)}
              </div>

              <div class="oa-cta-rule" style="margin:26px 0 0 0; border-top:1px solid #f2f1ec; padding-top:22px;">
                <a href="{_escape(kit_url)}" class="oa-cta" style="display:inline-block; padding:14px 22px; border-radius:24px; background:#132b26; color:#ffffff; font-family:{FONT_SANS}; font-size:14px; font-weight:500; text-decoration:none; line-height:1.2;">
                  {_escape(cta_label)}
                </a>
                <p style="margin:14px 0 0 0; font-family:{FONT_SANS}; font-size:13px; line-height:1.7; color:#6e7c77;">
                  {hint}
                </p>
              </div>
            </td>
          </tr>

          <tr>
            <td class="oa-footer" style="padding:20px 32px; border-top:1px solid #f2f1ec; background:#f7f6f2; font-family:{FONT_SANS}; font-size:12px; line-height:1.7; color:#8b9995;">
              Orderly Affairs · {footer_owner}<br/>
              {support_line}
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


async def send_email(to_email: str, subject: str, html: str) -> None:
    ses_send_email(
        to_emails=to_email,
        subject=subject,
        html_content=html,
    )
