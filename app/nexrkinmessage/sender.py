from datetime import datetime
import sendgrid
from sendgrid.helpers.mail import Mail

from app.security.crypto import decrypt_data
from app.database import messageofnextkin_collection
from app.config import settings


async def send_letter(letter: dict):
    payload = decrypt_data(letter["encrypted_payload"])

    html = f"""
    <div style="font-family:Arial,sans-serif">
      <h2>{letter['title']}</h2>
      <div>{payload.get("content","")}</div>
    """

    if letter.get("media"):
        html += f"""
        <p style="margin-top:16px">
          <a href="{letter['media']['url']}" target="_blank">
            ▶ View attached {letter['message_type']}
          </a>
        </p>
        """

    html += "</div>"

    message = Mail(
        from_email=settings.EMAIL_SENDER,
        to_emails=letter["recipient_email"],
        subject=payload.get("subject") or letter["title"],
        html_content=html,
    )

    try:
        sg = sendgrid.SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        sg.send(message)
    except Exception as e:
        print("❌ SendGrid error:", e)
        raise

    # ✅ Mark as sent (DO NOT DELETE FILES)
    await messageofnextkin_collection.update_one(
        {"_id": letter["_id"]},
        {
            "$set": {
                "status": "sent",
                "sent_at": datetime.utcnow(),
            }
        }
    )
