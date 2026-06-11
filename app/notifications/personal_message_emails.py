from __future__ import annotations

import html
from pathlib import Path

from bson import ObjectId
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import settings
from app.database import users_collection
from app.security.message_crypto import load_message

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "personal_message.html"
_MESSAGE_SUBJECT_HEADING = """
              <h1 style="margin:0 0 20px 0; font-size:24px; line-height:1.3; color:#213D59; font-weight:700;">
                {subject}
              </h1>
"""
_ATTACHMENT_BLOCK = """
          <!-- Optional attachment (audio/video/file) -->
          <tr>
            <td style="padding:16px 32px 32px 32px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f4f6f9; border-radius:6px;">
                <tr>
                  <td style="padding:20px 24px;">
                    <p style="margin:0 0 12px 0; font-size:14px; color:#213D59; font-weight:600;">
                      {sender_name} included an attachment for you
                    </p>
                    <a href="{url}" style="display:inline-block; background-color:#213D59; color:#ffffff; text-decoration:none; padding:12px 24px; border-radius:4px; font-size:15px; font-weight:600;">
                      View {attachment_type}
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
"""


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


def personal_message_subject(sender_name: str) -> str:
    return f"A message from {sender_name} for you"


def render_personal_message_html(
    *,
    sender_name: str,
    recipient_name: str,
    message_subject: str | None,
    message_body: str,
    attachment_url: str | None = None,
    attachment_type: str | None = None,
) -> str:
    template = _load_template()

    subject_heading = ""
    if message_subject and message_subject.strip():
        subject_heading = _MESSAGE_SUBJECT_HEADING.format(
            subject=html.escape(message_subject.strip())
        )

    attachment_block = ""
    if attachment_url:
        attachment_block = _ATTACHMENT_BLOCK.format(
            sender_name=html.escape(sender_name),
            url=html.escape(attachment_url, quote=True),
            attachment_type=html.escape(attachment_type or "file"),
        )

    replacements = {
        "{{sender_name}}": html.escape(sender_name),
        "{{recipient_name}}": html.escape(recipient_name),
        "{{message_subject_heading}}": subject_heading,
        "{{message_body}}": _format_message_body(message_body),
        "{{attachment_block}}": attachment_block,
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


def _sender_name(owner: dict | None) -> str:
    if not owner:
        return "Someone who cares about you"
    return owner.get("full_name") or owner.get("email") or "Someone who cares about you"


async def send_personal_message_email(*, letter: dict, owner: dict | None = None) -> None:
    letter = load_message(letter) or letter

    if owner is None:
        owner = await _resolve_owner(str(letter.get("owner_id") or ""))

    sender_name = _sender_name(owner)
    recipient_name = letter.get("recipient") or letter.get("recipient_email") or "there"
    message_subject = letter.get("subject") or letter.get("title")
    message_body = letter.get("content") or ""

    media = letter.get("media") or {}
    attachment_url = media.get("url")
    attachment_type = _attachment_label(letter.get("message_type"), media)

    html_content = render_personal_message_html(
        sender_name=sender_name,
        recipient_name=recipient_name,
        message_subject=message_subject,
        message_body=message_body,
        attachment_url=attachment_url,
        attachment_type=attachment_type,
    )

    sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.MESSAGES_FROM_EMAIL,
        to_emails=letter["recipient_email"],
        subject=personal_message_subject(sender_name),
        html_content=html_content,
    )

    sg.send(message)
