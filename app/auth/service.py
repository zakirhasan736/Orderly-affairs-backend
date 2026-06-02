from datetime import datetime, timezone

from bson import ObjectId

from app.database import db, messageofnextkin_collection, users_collection
from app.letters.email_utils import render_email_html, send_email
from app.nexrkinmessage.sender import send_letter

nok_letters_collection = db["nok_letters"]


async def _owner_refs(owner_ref: str) -> list[str]:
    refs = {owner_ref}
    owner = await users_collection.find_one({"email": owner_ref, "role": "owner"})

    if not owner:
        try:
            owner = await users_collection.find_one(
                {"_id": ObjectId(owner_ref), "role": "owner"}
            )
        except Exception:
            owner = None

    if owner:
        refs.add(str(owner["_id"]))
        if owner.get("email"):
            refs.add(owner["email"])

    return list(refs)


async def trigger_death_letters(owner_id: str):
    owner_refs = await _owner_refs(owner_id)

    letters = await messageofnextkin_collection.find({
        "owner_id": {"$in": owner_refs},
        "delivery_trigger": "death",
        "status": "pending",
        "is_deleted": False,
    }).to_list(None)

    for letter in letters:
        try:
            await send_letter(letter)
        except Exception as e:
            print(f"Failed death-trigger letter {letter['_id']}: {e}")

    nok_letters = await nok_letters_collection.find({
        "owner_id": {"$in": owner_refs},
        "delivery_status": {"$ne": "sent"},
        "$or": [
            {"delivery_trigger": "death"},
            {
                "delivery_trigger": {"$exists": False},
                "letter_date": {"$in": [None, ""]},
            },
        ],
    }).to_list(None)

    for letter in nok_letters:
        now = datetime.now(timezone.utc)
        try:
            claim = await nok_letters_collection.update_one(
                {"_id": letter["_id"], "delivery_status": {"$ne": "sent"}},
                {"$set": {"delivery_status": "processing", "updated_at": now}},
            )
            if getattr(claim, "modified_count", 0) != 1:
                continue

            to_email = letter.get("nok_email")
            if not to_email:
                raise RuntimeError("NOK email missing")

            html = render_email_html(letter)
            await send_email(to_email, "Letter to Next of Kin", html)

            await nok_letters_collection.update_one(
                {"_id": letter["_id"]},
                {
                    "$set": {
                        "delivery_status": "sent",
                        "sent_at": now,
                        "updated_at": now,
                    }
                },
            )
        except Exception as e:
            await nok_letters_collection.update_one(
                {"_id": letter["_id"]},
                {
                    "$set": {
                        "delivery_status": "pending",
                        "last_delivery_error": str(e),
                        "updated_at": now,
                    }
                },
            )
            print(f"Failed death-trigger NOK letter {letter['_id']}: {e}")
