from datetime import datetime
from app.database import messageofnextkin_collection
from .sender import send_letter

async def check_scheduled_letters():
    now = datetime.utcnow()

    letters = await messageofnextkin_collection.find({
        "delivery_trigger": "date",
        "delivery_date": {"$lte": now},
        "status": "pending",
        "is_deleted": False,
    }).to_list(None)

    for letter in letters:
        try:
            await send_letter(letter)
        except Exception as e:
            print(f"❌ Failed sending scheduled letter {letter['_id']}: {e}")

