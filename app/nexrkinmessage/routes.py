from fastapi import Header
from fastapi import APIRouter, Depends, HTTPException,  UploadFile, File
from datetime import datetime
from bson import ObjectId
from fastapi import Request
from app.security.jwt_handler import verify_token
from app.security.crypto import encrypt_data,decrypt_data
from app.database import messageofnextkin_collection
from .models import LetterCreate, LetterUpdate, MediaDeleteRequest
from app.security.cloudinary_service import upload_file, delete_file

router = APIRouter(prefix="/message", tags=["Message"])

MESSAGE_MEDIA_FOLDER = "messages/media/"

def delete_media_file(media: dict | None):
    if not media:
        return

    public_id = media.get("public_id")
    if public_id:
        delete_file(public_id)

@router.post("")
async def create_letter(
    payload: LetterCreate,
    authorization: str = Header(...)
):
    token = authorization.split(" ")[1]
    user = verify_token(token)
        # 🔒 ALWAYS use ObjectId string
    owner_id = user.get("owner_id") or user.get("sub")
    encrypted_payload = encrypt_data({
        "subject": payload.subject,
        "content": payload.content,
    })

    doc = {
        # "owner_id": user["sub"],
        "owner_id": owner_id,
        "title": payload.title,
        "encrypted_payload": encrypted_payload,

        "recipient": payload.recipient,
        "recipient_email": payload.recipient_email,

        "message_type": payload.message_type,
        "media": payload.media,

        "delivery_trigger": payload.delivery_trigger,
        "delivery_date": payload.delivery_date,
        "delivery_occasion": payload.delivery_occasion,  # ✅ SAVED

        "status": "pending",
        "is_deleted": False,

        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    await messageofnextkin_collection.insert_one(doc)
    return {"status": "saved"}


@router.get("")
async def get_letters(authorization: str = Header(...)):
    user = verify_token(authorization.split(" ")[1])
    owner_id = user.get("owner_id") or user.get("sub")
   
    letters = await messageofnextkin_collection.find({
        "owner_id": owner_id,
        "is_deleted": False,
    }).to_list(None)

    result = []

    for l in letters:
        payload = decrypt_data(l["encrypted_payload"])

        result.append({
            "_id": str(l["_id"]),
            "title": l["title"],

            # ✅ DECRYPTED DATA
            "subject": payload.get("subject"),
            "content": payload.get("content"),

            "recipient": l["recipient"],
            "recipient_email": l["recipient_email"],

            "message_type": l["message_type"],
            "media": l.get("media"),

            "delivery_trigger": l["delivery_trigger"],
            "delivery_date": l.get("delivery_date"),
            "delivery_occasion": l.get("delivery_occasion"),

            "status": l["status"],
            "sent_at": l.get("sent_at"),
            "updated_at": l["updated_at"],
        })

    return result

@router.put("/{letter_id}")
async def update_letter(
    letter_id: str,
    payload: LetterUpdate,
    authorization: str = Header(...)
):
    user = verify_token(authorization.split(" ")[1])

    owner_id = user.get("owner_id") or user.get("sub")

    letter = await messageofnextkin_collection.find_one({
        "_id": ObjectId(letter_id),
        "owner_id": owner_id,
        "is_deleted": False,
    })

    if not letter:
        raise HTTPException(status_code=404, detail="Letter not found")

    update_data = payload.model_dump(exclude_unset=True)

    if "media" in update_data:
        old_media = letter.get("media")
        new_media = update_data.get("media")
        old_public_id = (old_media or {}).get("public_id")
        new_public_id = (new_media or {}).get("public_id")

        if old_public_id and old_public_id != new_public_id:
            delete_media_file(old_media)

    # Encrypt only if content/subject changed
    if "subject" in update_data or "content" in update_data:
        encrypted_payload = encrypt_data({
            "subject": update_data.pop("subject", None),
            "content": update_data.pop("content", None),
        })
        update_data["encrypted_payload"] = encrypted_payload

    update_data["updated_at"] = datetime.utcnow()

    result = await messageofnextkin_collection.update_one(
        {
            "_id": ObjectId(letter_id),
            "owner_id": owner_id,
            "is_deleted": False,
        },
        {"$set": update_data}
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Letter not found")

    return {"status": "updated"}

@router.delete("/{letter_id}")
async def delete_letter(letter_id: str, authorization: str = Header(...)):
    user = verify_token(authorization.split(" ")[1])

    owner_id = user.get("owner_id") or user.get("sub")
    letter = await messageofnextkin_collection.find_one({
        "_id": ObjectId(letter_id),
        "owner_id": owner_id,
        "is_deleted": False,
    })

    if not letter:
        raise HTTPException(status_code=404, detail="Letter not found")

    delete_media_file(letter.get("media"))

    await messageofnextkin_collection.update_one(
        {"_id": ObjectId(letter_id), "owner_id": owner_id},
        {
            "$set": {
                "is_deleted": True,
                "media": None,
                "updated_at": datetime.utcnow(),
            }
        }
    )

    return {"status": "deleted"}

@router.delete("/{letter_id}/media")
async def delete_letter_media(letter_id: str, authorization: str = Header(...)):
    user = verify_token(authorization.split(" ")[1])

    owner_id = user.get("owner_id") or user.get("sub")
    letter = await messageofnextkin_collection.find_one({
        "_id": ObjectId(letter_id),
        "owner_id": owner_id,
        "is_deleted": False,
    })

    if not letter:
        raise HTTPException(status_code=404, detail="Letter not found")

    delete_media_file(letter.get("media"))

    await messageofnextkin_collection.update_one(
        {"_id": ObjectId(letter_id), "owner_id": owner_id},
        {"$set": {"media": None, "updated_at": datetime.utcnow()}}
    )

    return {"status": "media_deleted"}

@router.post("/media")
async def upload_message_media(
    file: UploadFile = File(...),
    authorization: str = Header(...)
):
    token = authorization.split(" ")[1]
    verify_token(token)

    if not file.content_type.startswith(("video/", "audio/")):
        raise HTTPException(
            status_code=400,
            detail="Only audio/video files are allowed"
        )

    uploaded = upload_file(
        file.file,
        folder="messages/media"
    )

    return {
        "url": uploaded["secure_url"],
        "public_id": uploaded["public_id"],
        "type": uploaded["resource_type"],
        "format": uploaded.get("format"),
        "size": uploaded.get("bytes"),
    }

@router.post("/media/delete")
async def delete_uploaded_message_media(
    payload: MediaDeleteRequest,
    authorization: str = Header(...)
):
    token = authorization.split(" ")[1]
    verify_token(token)

    if not payload.public_id.startswith(MESSAGE_MEDIA_FOLDER):
        raise HTTPException(
            status_code=400,
            detail="Only message media can be deleted from this endpoint"
        )

    delete_file(payload.public_id)

    return {"status": "deleted"}

