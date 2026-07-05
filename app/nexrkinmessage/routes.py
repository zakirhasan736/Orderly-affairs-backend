from app.security.token_resolver import decode_access_token
from fastapi import APIRouter, HTTPException, UploadFile, File, Header, Query, Request
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId
from app.security.message_crypto import load_message, prepare_message_for_storage
from app.database import messageofnextkin_collection
from .models import LetterCreate, LetterUpdate, MediaDeleteRequest
from app.security.cloudinary_service import (
    MESSAGE_MEDIA_FOLDER,
    MESSAGE_MEDIA_MAX_BYTES,
    delete_file,
    generate_message_media_upload_signature,
    upload_media_file,
    validate_message_media_size,
)

router = APIRouter(prefix="/message", tags=["Message"])

MESSAGE_MEDIA_EXTENSIONS = (
    ".mp4", ".mov", ".webm", ".m4v",
    ".mp3", ".m4a", ".wav", ".aac", ".ogg",
)


def is_allowed_message_media(file: UploadFile) -> bool:
    content_type = (file.content_type or "").lower()
    filename = (file.filename or "").lower()

    if content_type.startswith(("video/", "audio/")):
        return True

    if content_type in {"", "application/octet-stream"}:
        return filename.endswith(MESSAGE_MEDIA_EXTENSIONS)

    return filename.endswith(MESSAGE_MEDIA_EXTENSIONS)


def parse_message_id(letter_id: str) -> ObjectId:
    try:
        return ObjectId(letter_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid message id")


def get_authenticated_user(request: Request, authorization: str | None = None):
    return decode_access_token(request, authorization)


def delete_media_file(media: dict | None) -> bool:
    if not media:
        return True

    public_id = media.get("public_id")
    if not public_id:
        return True

    deleted = delete_file(public_id, media.get("type"))
    if not deleted:
        print(f"⚠️ Failed to hard-delete message media from Cloudinary: {public_id}")

    return deleted

@router.post("")
async def create_letter(
    payload: LetterCreate,
    request: Request, authorization: str | None = Header(default=None)
):
    user = decode_access_token(request, authorization)
    owner_id = user.get("owner_id") or user.get("sub")

    doc = prepare_message_for_storage({
        "owner_id": owner_id,
        "title": payload.title,
        "subject": payload.subject,
        "content": payload.content,
        "recipient": payload.recipient,
        "recipient_email": payload.recipient_email,
        "message_type": payload.message_type,
        "media": payload.media,
        "delivery_trigger": payload.delivery_trigger,
        "delivery_date": payload.delivery_date,
        "delivery_occasion": payload.delivery_occasion,
        "status": "pending",
        "is_deleted": False,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    })

    result = await messageofnextkin_collection.insert_one(doc)
    return {"status": "saved", "_id": str(result.inserted_id)}


@router.get("")
async def get_letters(request: Request, authorization: str | None = Header(default=None)):
    user = decode_access_token(request, authorization)
    owner_id = user.get("owner_id") or user.get("sub")
   
    letters = await messageofnextkin_collection.find({
        "owner_id": owner_id,
        "is_deleted": False,
    }).to_list(None)

    result = []

    for letter in letters:
        decrypted = load_message(letter)
        result.append({
            "_id": str(decrypted["_id"]),
            "title": decrypted.get("title"),
            "subject": decrypted.get("subject"),
            "content": decrypted.get("content"),
            "recipient": decrypted.get("recipient"),
            "recipient_email": decrypted["recipient_email"],
            "message_type": decrypted["message_type"],
            "media": decrypted.get("media"),
            "delivery_trigger": decrypted["delivery_trigger"],
            "delivery_date": decrypted.get("delivery_date"),
            "delivery_occasion": decrypted.get("delivery_occasion"),
            "status": decrypted["status"],
            "sent_at": decrypted.get("sent_at"),
            "updated_at": decrypted["updated_at"],
        })

    return result

@router.delete("")
async def delete_all_letters(request: Request, authorization: str | None = Header(default=None)):
    user = decode_access_token(request, authorization)
    owner_id = user.get("owner_id") or user.get("sub")

    letters = await messageofnextkin_collection.find({
        "owner_id": owner_id,
        "is_deleted": False,
    }).to_list(None)

    for letter in letters:
        delete_media_file(letter.get("media"))

    if letters:
        await messageofnextkin_collection.update_many(
            {"owner_id": owner_id, "is_deleted": False},
            {
                "$set": {
                    "is_deleted": True,
                    "media": None,
                    "updated_at": datetime.utcnow(),
                }
            },
        )

    return {"status": "cleared", "count": len(letters)}

@router.put("/{letter_id}")
async def update_letter(
    letter_id: str,
    payload: LetterUpdate,
    request: Request, authorization: str | None = Header(default=None)
):
    user = get_authenticated_user(request, authorization)
    owner_id = user.get("owner_id") or user.get("sub")
    letter_oid = parse_message_id(letter_id)

    letter = await messageofnextkin_collection.find_one({
        "_id": letter_oid,
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

    merged = load_message(letter)
    merged.update(update_data)
    merged["owner_id"] = owner_id
    merged["updated_at"] = datetime.utcnow()

    stored = prepare_message_for_storage(merged)
    unset = {
        key: ""
        for key in ("title", "subject", "content", "recipient")
        if key in letter
    }

    update_doc: dict = {"$set": stored}
    if unset:
        update_doc["$unset"] = unset

    result = await messageofnextkin_collection.update_one(
        {
            "_id": letter_oid,
            "owner_id": owner_id,
            "is_deleted": False,
        },
        update_doc,
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Letter not found")

    return {"status": "updated"}

@router.delete("/{letter_id}")
async def delete_letter(letter_id: str, request: Request, authorization: str | None = Header(default=None)):
    user = get_authenticated_user(request, authorization)
    owner_id = user.get("owner_id") or user.get("sub")
    letter_oid = parse_message_id(letter_id)

    letter = await messageofnextkin_collection.find_one({
        "_id": letter_oid,
        "owner_id": owner_id,
        "is_deleted": False,
    })

    if not letter:
        raise HTTPException(status_code=404, detail="Letter not found")

    delete_media_file(letter.get("media"))

    await messageofnextkin_collection.update_one(
        {"_id": letter_oid, "owner_id": owner_id},
        {
            "$set": {
                "is_deleted": True,
                "media": None,
                "updated_at": datetime.utcnow(),
            }
        }
    )

    return {"status": "deleted", "media_removed": True}

@router.delete("/{letter_id}/media")
async def delete_letter_media(letter_id: str, request: Request, authorization: str | None = Header(default=None)):
    user = get_authenticated_user(request, authorization)
    owner_id = user.get("owner_id") or user.get("sub")
    letter_oid = parse_message_id(letter_id)

    letter = await messageofnextkin_collection.find_one({
        "_id": letter_oid,
        "owner_id": owner_id,
        "is_deleted": False,
    })

    if not letter:
        raise HTTPException(status_code=404, detail="Letter not found")

    delete_media_file(letter.get("media"))

    await messageofnextkin_collection.update_one(
        {"_id": letter_oid, "owner_id": owner_id},
        {"$set": {"media": None, "updated_at": datetime.utcnow()}}
    )

    return {"status": "media_deleted"}

@router.get("/media/signature")
async def get_message_media_upload_signature(
    request: Request, authorization: str | None = Header(default=None),
    file_size: int = Query(..., ge=1),
    resource_type: str = Query("video"),
):
    decode_access_token(request, authorization)

    try:
        validate_message_media_size(file_size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    normalized = resource_type if resource_type in ("video", "image") else "video"
    return generate_message_media_upload_signature(resource_type=normalized)


@router.post("/media")
async def upload_message_media(
    request: Request,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    decode_access_token(request, authorization)

    if not is_allowed_message_media(file):
        raise HTTPException(
            status_code=400,
            detail="Only audio/video files are allowed"
        )

    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)

    try:
        validate_message_media_size(size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    uploaded = upload_media_file(
        file.file,
        folder=MESSAGE_MEDIA_FOLDER,
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
    request: Request, authorization: str | None = Header(default=None)
):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner only")

    if not payload.public_id.startswith(MESSAGE_MEDIA_FOLDER):
        raise HTTPException(
            status_code=400,
            detail="Only message media can be deleted from this endpoint",
        )

    owner_id = decoded.get("owner_id") or decoded.get("sub")
    if decoded.get("role") == "owner":
        from app.database import users_collection
        owner = await users_collection.find_one({"email": decoded["sub"], "role": "owner"})
        if owner:
            owner_id = str(owner["_id"])

    owned = await messageofnextkin_collection.find_one({
        "owner_id": str(owner_id),
        "media.public_id": payload.public_id,
        "is_deleted": False,
    })
    if not owned:
        raise HTTPException(status_code=403, detail="Not authorized to delete this file")

    delete_file(payload.public_id, payload.resource_type)

    return {"status": "deleted"}
