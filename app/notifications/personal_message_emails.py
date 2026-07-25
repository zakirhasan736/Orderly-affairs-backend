from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from bson import ObjectId
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import settings
from app.database import users_collection
from app.notifications.display_names import resolve_owner_display_name
from app.notifications.email_layout import brand_logo_url, portal_url
from app.security.message_crypto import load_message

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "personal_message.html"

FONT_SANS = (
    "'Instrument Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',"
    "Helvetica,Arial,sans-serif"
)


def _load_template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _format_message_body(content: str) -> str:
    if not content:
        return ""
    return html.escape(content).replace("\n", "<br/>")


def _attachment_label(message_type: str | None, media: dict | None) -> str:
    if message_type in {"video", "audio", "letter"}:
        return message_type
    if media and media.get("type"):
        return str(media["type"])
    return "file"


def _fmt_date(value) -> str:
    if value is None:
        return datetime.utcnow().strftime("%b %d, %Y").replace(" 0", " ")
    if isinstance(value, datetime):
        dt = value.replace(tzinfo=None) if value.tzinfo else value
        return dt.strftime("%b %d, %Y").replace(" 0", " ")
    text = str(value).strip()
    if not text:
        return datetime.utcnow().strftime("%b %d, %Y").replace(" 0", " ")
    try:
        if "T" in text:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
            return dt.strftime("%b %d, %Y").replace(" 0", " ")
    except Exception:
        pass
    return text


def _fmt_duration(media: dict | None) -> str | None:
    if not media:
        return None
    raw = media.get("duration") or media.get("duration_seconds") or media.get("length")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        text = str(raw).strip()
        return text or None
    total = int(round(seconds))
    mins, secs = divmod(max(total, 0), 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def _type_label(message_type: str) -> str:
    if message_type == "audio":
        return "Audio"
    if message_type == "video":
        return "Video"
    return "Message"


def personal_message_subject(sender_name: str, *, message_type: str = "letter") -> str:
    if message_type in {"video", "audio"}:
        return f"{sender_name} left a recording for you"
    return f"{sender_name} left a message for you"


def _media_card(
    *,
    title: str,
    meta: str,
    hint: str,
) -> str:
    safe_title = html.escape(title)
    safe_meta = html.escape(meta)
    safe_hint = html.escape(hint)
    # Play glyph in ink circle (more reliable than SVG across email clients)
    return f"""
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:22px 0; border:1px solid #e4e6e1; border-radius:12px; overflow:hidden;">
                <tr>
                  <td style="padding:22px; background:#f7f6f2;">
                    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                      <tr>
                        <td valign="middle" width="52" style="padding-right:16px;">
                          <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:52px; height:52px; background:#132b26; border-radius:50%;">
                            <tr>
                              <td align="center" valign="middle" style="width:52px; height:52px; color:#ffffff; font-size:18px; line-height:1;">
                                &#9658;
                              </td>
                            </tr>
                          </table>
                        </td>
                        <td valign="middle" style="font-family:{FONT_SANS};">
                          <p style="margin:0; font-size:15.5px; font-weight:600; color:#132b26;">&ldquo;{safe_title}&rdquo;</p>
                          <p style="margin:4px 0 0 0; font-size:13px; color:#6e7c77;">{safe_meta}</p>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:16px 22px; font-family:{FONT_SANS}; font-size:13px; color:#6e7c77; line-height:1.6;">
                    {safe_hint}
                  </td>
                </tr>
              </table>
"""


def _letter_block(body_html: str) -> str:
    if not body_html:
        return ""
    return f"""
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:22px 0; border:1px solid #e4e6e1; border-radius:12px; overflow:hidden;">
                <tr>
                  <td style="padding:22px; background:#f7f6f2; font-family:{FONT_SANS}; font-size:15px; line-height:1.7; color:#132b26;">
                    {body_html}
                  </td>
                </tr>
              </table>
"""


def render_personal_message_html(
    *,
    sender_name: str,
    recipient_name: str,
    message_subject: str | None,
    message_body: str,
    attachment_url: str | None = None,
    attachment_type: str | None = None,
    recorded_at: datetime | str | None = None,
    released_at: datetime | str | None = None,
    duration_label: str | None = None,
    cta_url: str | None = None,
    note_text: str | None = None,
) -> str:
    template = _load_template()
    portal = portal_url()
    msg_type = (attachment_type or "letter").lower()
    is_recording = msg_type in {"video", "audio"}

    title_text = (message_subject or "").strip() or (
        "a recording" if is_recording else "a message"
    )
    first_name = (recipient_name or "there").strip() or "there"

    if is_recording:
        headline = f"{sender_name} left a recording for you."
        intro = (
            f"Hello {first_name} — this was recorded for you and released now, "
            f"as {sender_name} asked."
        )
        type_word = _type_label(msg_type)
        meta_parts = [type_word]
        if duration_label:
            meta_parts.append(duration_label)
        if recorded_at:
            meta_parts.append(f"recorded {_fmt_date(recorded_at)}")
        meta = " · ".join(meta_parts)
        media_or_letter = _media_card(
            title=title_text,
            meta=meta,
            hint=(
                "Watch it privately, whenever you're ready. The link stays valid "
                "— you don't have to do it today."
                if msg_type == "video"
                else "Listen privately, whenever you're ready. The link stays valid "
                "— you don't have to do it today."
            ),
        )
        cta_label = (
            "Watch the recording" if msg_type == "video" else "Listen to the recording"
        )
        link = attachment_url or portal
    else:
        headline = f"{sender_name} left a message for you."
        intro = (
            f"Hello {first_name} — this was written for you and released now, "
            f"as {sender_name} asked."
        )
        media_or_letter = _letter_block(_format_message_body(message_body))
        if not media_or_letter and attachment_url:
            media_or_letter = _media_card(
                title=title_text,
                meta="Message",
                hint="Open it privately, whenever you're ready.",
            )
        cta_label = "Open the message"
        link = attachment_url or portal

    note_html = ""
    if note_text and note_text.strip():
        note_html = (
            f'<p style="margin:22px 0 0 0; font-family:{FONT_SANS}; '
            f'font-size:13.5px; line-height:1.7; color:#6e7c77;">'
            f"{html.escape(note_text.strip())}</p>"
        )

    footer_text = (
        f"Orderly Affairs · Released from {html.escape(sender_name)}'s kit on "
        f"{html.escape(_fmt_date(released_at))}."
    )

    replacements = {
        "{{page_title}}": html.escape(personal_message_subject(sender_name, message_type=msg_type)),
        "{{preheader}}": html.escape(
            f"{sender_name} prepared something for you through Orderly Affairs."
        ),
        "{{logo_url}}": html.escape(brand_logo_url(), quote=True),
        "{{headline}}": html.escape(headline),
        "{{intro}}": html.escape(intro),
        "{{media_or_letter_block}}": media_or_letter,
        "{{cta_url}}": html.escape(cta_url or link or portal, quote=True),
        "{{cta_label}}": html.escape(cta_label),
        "{{note_html}}": note_html,
        "{{footer_text}}": footer_text,
    }

    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)

    return rendered


async def _resolve_owner(owner_ref: str) -> dict | None:
    if not owner_ref:
        return None

    owner = None
    try:
        owner = await users_collection.find_one(
            {"_id": ObjectId(owner_ref), "role": "owner"}
        )
    except Exception:
        owner = None

    if not owner:
        owner = await users_collection.find_one(
            {"email": owner_ref, "role": "owner"}
        )

    return owner


async def _sender_name(owner: dict | None) -> str:
    if not owner:
        return "Someone who cares about you"
    return await resolve_owner_display_name(owner)


async def send_personal_message_email(*, letter: dict, owner: dict | None = None) -> None:
    letter = load_message(letter) or letter

    if owner is None:
        owner = await _resolve_owner(str(letter.get("owner_id") or ""))

    sender_name = await _sender_name(owner)
    recipient_name = letter.get("recipient") or letter.get("recipient_email") or "there"
    message_subject = letter.get("subject") or letter.get("title")
    message_body = letter.get("content") or ""

    media = letter.get("media") or {}
    attachment_url = media.get("url")
    attachment_type = _attachment_label(letter.get("message_type"), media)
    duration_label = _fmt_duration(media if isinstance(media, dict) else None)
    recorded_at = (
        media.get("recorded_at")
        if isinstance(media, dict)
        else None
    ) or letter.get("created_at")
    released_at = letter.get("sent_at") or datetime.utcnow()

    html_content = render_personal_message_html(
        sender_name=sender_name,
        recipient_name=recipient_name,
        message_subject=message_subject,
        message_body=message_body,
        attachment_url=attachment_url,
        attachment_type=attachment_type,
        recorded_at=recorded_at,
        released_at=released_at,
        duration_label=duration_label,
        cta_url=attachment_url or portal_url(),
    )

    sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.MESSAGES_FROM_EMAIL,
        to_emails=letter["recipient_email"],
        subject=personal_message_subject(sender_name, message_type=attachment_type),
        html_content=html_content,
    )

    sg.send(message)
