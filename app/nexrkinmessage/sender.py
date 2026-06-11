from datetime import datetime

from app.database import messageofnextkin_collection
from app.notifications.personal_message_emails import send_personal_message_email


async def send_letter(letter: dict):
    await send_personal_message_email(letter=letter)

    await messageofnextkin_collection.update_one(
        {"_id": letter["_id"]},
        {
            "$set": {
                "status": "sent",
                "sent_at": datetime.utcnow(),
            }
        }
    )
