from app.database import messageofnextkin_collection
from app.nexrkinmessage.sender import send_letter

async def trigger_death_letters(owner_id: str):
    letters = await messageofnextkin_collection.find({
        "owner_id": owner_id,
        "delivery_trigger": "death",
        "status": "pending",
        "is_deleted": False,
    }).to_list(None)

    for letter in letters:
        try:
            await send_letter(letter)
        except Exception as e:
            print(f"❌ Failed death-trigger letter {letter['_id']}: {e}")
