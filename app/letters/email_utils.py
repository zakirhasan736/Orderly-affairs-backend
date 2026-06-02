# app/nok_letter/email_utils.py
from __future__ import annotations
from typing import Dict, Any
from datetime import datetime
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from app.config import settings

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

    return f"""{fmt_date(doc.get("letter_date"))}

{doc.get("letter_greeting") or "Dear"} {doc.get("letter_to") or "[Next of Kin Name]"},


{doc.get("letter_opening") or "I'm writing you this note as someone I trust deeply.\n\nAs my next of kin, the executor of my will, a close friend, my attorney, or someone who cares—I want you to know that I've prepared something to help guide you through what comes next."}

{doc.get("kit_description") or "I've subscribed to an Orderly Affairs Kit. Inside, you'll find everything you may need to manage my affairs if I'm no longer able to, or when I'm gone. It includes not only documents, but also instructions—gentle step-by-step guides to make this process less overwhelming."}

You can access the kit online at: {doc.get("access_url") or "https://orderly-affairs.com"}

{doc.get("login_credentials_text") or default_login_line}

{doc.get("accessible_sections") or "Once you log in, you'll be able to manage the sections below on my behalf:\n\n(Autofill sections based on selection in the access management section)"}

In addition to the online kit, you'll find two important physical items:

{doc.get("key_bag_info") or "• The Key Bag: This contains important keys and a guide to what each is for. It may include house keys, PO box keys, or vehicle keys. It is located"} {doc.get("key_bag_location") or "[Key Bag Location]"}.

{doc.get("documents_bag_info") or "• The Documents Bag: Please keep this safe. It contains original documents and space to store items such as death certificates. You may need to refer to it even after everything has been settled. It is located"} {doc.get("documents_bag_location") or "[Documents Bag Location]"}.

{doc.get("incomplete_kit_message") or "If any part of the kit is incomplete, please don't worry. Even the unfinished parts can still help you stay organized. I've done my best to make sure you won't be left searching through drawers or wondering where things are."}

{doc.get("closing_message") or "Above all, this kit is my way of caring for you—even when I can't be here in person.\n\nTake your time. Breathe. You've got this, and I'm grateful it's you."}

{doc.get("letter_signature") or "With love,"}

[Your signature]
"""

def render_email_html(doc: Dict[str, Any]) -> str:
    # simple HTML wrapper
    body = render_letter_text(doc).replace("\n", "<br/>")
    owner = doc.get("owner_id", "")
    return f"""
    <div style="font-family:Arial,sans-serif;line-height:1.6;color:#111">
      <p style="color:#666;font-size:12px;margin:0 0 10px 0">Owner ID: {owner}</p>
      <h2 style="margin:0 0 12px 0">Letter to Next of Kin</h2>
      <div>{body}</div>
      <hr style="margin:24px 0;border:none;border-top:1px solid #eee"/>
      <p style="font-size:12px;color:#666">Sent by Orderly Affairs</p>
    </div>
    """

async def send_email(to_email: str, subject: str, html: str) -> None:
    sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=to_email,
        subject=subject,
        html_content=html,
    )
    sg.send(message)
