from app.security.token_resolver import decode_owner_or_nok_token
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Header, Query, Request
from datetime import datetime
from bson import ObjectId
from bson.errors import InvalidId
from app.security.message_crypto import load_message, prepare_message_for_storage
from app.database import messageofnextkin_collection, users_collection
from .models import LetterCreate, LetterUpdate, MediaDeleteRequest
from app.config import settings
from app.security.cloudinary_service import (
    MESSAGE_MEDIA_FOLDER,
    MESSAGE_MEDIA_MAX_BYTES,
    delete_file,
    signed_media_delivery_url,
    validate_message_media_size,
)
from app.storage.message_s3 import (
    delete_message_s3_object,
    key_belongs_to_owner_folder,
    message_s3_prefix,
    presign_message_get_url,
    upload_message_bytes_to_s3,
)
from app.storage.vault import get_or_create_folder_uuid, vault_quota_check

router = APIRouter(prefix="/message", tags=["Message"])

MESSAGE_MEDIA_EXTENSIONS = (
    ".mp4", ".mov", ".webm", ".m4v",
    ".mp3", ".m4a", ".wav", ".aac", ".ogg",
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif",
)

MESSAGE_MEDIA_MIME_PREFIXES = ("audio/", "video/", "image/")


def is_allowed_message_media(file: UploadFile) -> bool:
    """Accept audio / video / image uploads for personal messages."""
    content_type = (file.content_type or "").lower().split(";", 1)[0].strip()
    if any(content_type.startswith(prefix) for prefix in MESSAGE_MEDIA_MIME_PREFIXES):
        return True

    filename = (file.filename or "").lower()
    if any(filename.endswith(ext) for ext in MESSAGE_MEDIA_EXTENSIONS):
        return True

    # Recorders sometimes send empty / octet-stream MIME with a .webm blob.
    if content_type in {"", "application/octet-stream", "binary/octet-stream"}:
        return any(
            token in filename
            for token in ("audio-", "video-", "photo-", ".webm", ".mp4", ".m4a")
        )
    return False


def classify_message_media_kind(
    file: UploadFile,
    *,
    kind_hint: str | None = None,
) -> str:
    hint = str(kind_hint or "").strip().lower()
    if hint in {"audio", "video", "image"}:
        return hint

    content_type = (file.content_type or "").lower().split(";", 1)[0].strip()
    filename = (file.filename or "").lower()

    if content_type.startswith("image/") or filename.endswith(
        (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif")
    ) or filename.startswith("photo-"):
        return "image"

    # Filename from recorder / picker wins when browsers mislabel MIME
    # (e.g. audio clips as video/webm).
    if filename.startswith("audio-") or filename.endswith(
        (".mp3", ".m4a", ".wav", ".aac", ".ogg")
    ):
        return "audio"
    if filename.startswith("video-"):
        return "video"

    if content_type.startswith("audio/"):
        return "audio"

    if content_type.startswith("video/"):
        return "video"

    # Extension fallback when MIME is missing / octet-stream.
    if filename.endswith((".mp4", ".mov", ".m4v", ".webm")):
        return "video"

    return "video"


def parse_message_id(letter_id: str) -> ObjectId:
    try:
        return ObjectId(letter_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid message id")


def media_identity(media: dict | None) -> str | None:
    if not isinstance(media, dict):
        return None
    return (
        str(media.get("s3_key") or "").strip()
        or str(media.get("public_id") or "").strip()
        or None
    )


def refresh_media_delivery(media: dict | None) -> dict | None:
    """Attach a fresh playback URL for S3 (or leave Cloudinary URL as-is)."""
    if not isinstance(media, dict):
        return media
    s3_key = str(media.get("s3_key") or "").strip()
    if not s3_key and str(media.get("storage") or "").lower() != "s3":
        return media
    if not s3_key:
        s3_key = str(media.get("public_id") or "").strip()
    if not s3_key:
        return media
    url = presign_message_get_url(
        s3_key=s3_key,
        bucket=str(media.get("s3_bucket") or "").strip() or None,
        expires_in=900,
    )
    if not url:
        return media
    refreshed = dict(media)
    refreshed["url"] = url
    refreshed["url_expires_in"] = 900
    refreshed["storage"] = refreshed.get("storage") or "s3"
    if not refreshed.get("s3_key"):
        refreshed["s3_key"] = s3_key
    return refreshed


def _message_media_s3_key(media: dict | None) -> str:
    if not isinstance(media, dict):
        return ""
    return (
        str(media.get("s3_key") or "").strip()
        or str(media.get("public_id") or "").strip()
    )


def _looks_like_message_s3_key(key: str) -> bool:
    key = str(key or "").strip().replace("\\", "/")
    if not key:
        return False
    prefix = message_s3_prefix()
    if prefix and (key == prefix or key.startswith(prefix + "/")):
        return True
    # Broader fallback for older / env-mismatched prefixes.
    return "/messages/" in key or key.startswith("orderly-affairs/messages")


def delete_media_file(media: dict | None) -> bool:
    """Delete remote bytes for message media (S3 and/or Cloudinary)."""
    if not isinstance(media, dict):
        return True

    ok = True
    s3_key = str(media.get("s3_key") or "").strip()
    storage = str(media.get("storage") or "").lower()
    public_id = str(media.get("public_id") or "").strip()
    key = s3_key or public_id
    bucket = str(media.get("s3_bucket") or "").strip() or None

    is_s3 = storage == "s3" or bool(s3_key) or _looks_like_message_s3_key(key)
    if is_s3 and key:
        if not delete_message_s3_object(s3_key=key, bucket=bucket):
            print(f"⚠️ Failed to delete message media from S3: {key}")
            ok = False
        return ok

    if public_id and not _looks_like_message_s3_key(public_id):
        deleted = delete_file(public_id, media.get("type"))
        if not deleted:
            print(f"⚠️ Failed to hard-delete message media from Cloudinary: {public_id}")
            ok = False

    return ok


def _letter_media(letter: dict | None) -> dict | None:
    """Resolve media dict from a raw Mongo letter document."""
    if not letter:
        return None
    decrypted = load_message(letter) or letter
    media = decrypted.get("media")
    return media if isinstance(media, dict) else None


async def resolve_message_owner_id(
    request: Request,
    authorization: str | None = None,
    *,
    write: bool = False,
) -> str:
    """Owner email used as message owner_id; family may act on granted section 4."""
    from app.auth.vault_actor import require_owner_or_family

    decoded = decode_owner_or_nok_token(request, authorization)
    if decoded.get("role") == "owner":
        return decoded["sub"]

    kwargs = {"area_id": "4", "detail": "No access to messages"}
    if write:
        kwargs["perm"] = "can_write"
    _, owner = await require_owner_or_family(decoded, **kwargs)
    return owner["email"]


async def load_owner_user_by_email(owner_email: str) -> dict:
    email = (owner_email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="Invalid owner")
    user = await users_collection.find_one({"email": email, "role": "owner"})
    if not user:
        # Some owners may omit role or use mixed-case email only.
        user = await users_collection.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="Owner account not found")
    return user

@router.post("")
async def create_letter(
    payload: LetterCreate,
    request: Request, authorization: str | None = Header(default=None)
):
    owner_id = await resolve_message_owner_id(request, authorization, write=True)

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
    owner_id = await resolve_message_owner_id(request, authorization, write=False)

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
            "media": refresh_media_delivery(decrypted.get("media")),
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
    owner_id = await resolve_message_owner_id(request, authorization, write=True)

    letters = await messageofnextkin_collection.find({
        "owner_id": owner_id,
        "is_deleted": False,
    }).to_list(None)

    for letter in letters:
        delete_media_file(_letter_media(letter))

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
    owner_id = await resolve_message_owner_id(request, authorization, write=True)
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
        old_media = _letter_media(letter)
        new_media = update_data.get("media")
        old_id = media_identity(old_media)
        new_id = media_identity(new_media if isinstance(new_media, dict) else None)

        # Replace or clear: delete previous remote object when identity changes.
        if old_id and old_id != new_id:
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
    owner_id = await resolve_message_owner_id(request, authorization, write=True)
    letter_oid = parse_message_id(letter_id)

    letter = await messageofnextkin_collection.find_one({
        "_id": letter_oid,
        "owner_id": owner_id,
        "is_deleted": False,
    })

    if not letter:
        raise HTTPException(status_code=404, detail="Letter not found")

    delete_media_file(_letter_media(letter))

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
    owner_id = await resolve_message_owner_id(request, authorization, write=True)
    letter_oid = parse_message_id(letter_id)

    letter = await messageofnextkin_collection.find_one({
        "_id": letter_oid,
        "owner_id": owner_id,
        "is_deleted": False,
    })

    if not letter:
        raise HTTPException(status_code=404, detail="Letter not found")

    delete_media_file(_letter_media(letter))

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
    await resolve_message_owner_id(request, authorization, write=True)

    try:
        validate_message_media_size(file_size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Prefer API → S3 uploads; Cloudinary direct upload is retired.
    if not settings.message_s3_active:
        raise HTTPException(
            status_code=503,
            detail="Media storage is not configured (S3).",
        )
    return {
        "storage": "s3",
        "use_api_upload": True,
        "folder": f"{message_s3_prefix()}/{{owner}}",
        "resource_type": resource_type if resource_type in ("video", "image") else "video",
        "max_bytes": MESSAGE_MEDIA_MAX_BYTES,
        "s3_bucket": settings.message_s3_bucket_name,
        "s3_prefix": message_s3_prefix(),
    }

async def _caller_owns_message_media(owner_id: str, *, public_id: str | None = None, s3_key: str | None = None) -> bool:
    """True when this owner's messages reference the asset (or orphan under their folder)."""
    from app.database import letters_collection

    key = (s3_key or public_id or "").strip()
    if not key:
        return False

    query_or = [{"media.public_id": key}, {"media.s3_key": key}]
    msg = await messageofnextkin_collection.find_one(
        {
            "owner_id": owner_id,
            "is_deleted": {"$ne": True},
            "$or": query_or,
        },
        {"_id": 1},
    )
    if msg:
        return True

    letter = await letters_collection.find_one(
        {
            "owner_id": owner_id,
            "$or": [{"media.public_id": key}, {"media.s3_key": key}],
        },
        {"_id": 1},
    )
    if letter:
        return True

    from app.database import db

    nok_letters = db["nok_letters"]
    nok = await nok_letters.find_one(
        {
            "owner_id": owner_id,
            "$or": [
                {"media.public_id": key},
                {"media.s3_key": key},
                {"attachment.public_id": key},
                {"attachment.s3_key": key},
            ],
        },
        {"_id": 1},
    )
    return bool(nok)


@router.post("/media/signed-url")
async def refresh_message_media_signed_url(
    data: dict,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Fresh signed / presigned URL for letter/message media (owner-scoped)."""
    owner_id = await resolve_message_owner_id(request, authorization, write=False)
    public_id = str(data.get("public_id") or "").strip()
    s3_key = str(data.get("s3_key") or "").strip()
    key = s3_key or public_id
    if not key:
        raise HTTPException(status_code=400, detail="public_id or s3_key required")
    if not owner_id:
        raise HTTPException(status_code=403, detail="Not authorized for this media")

    # S3 path
    if (
        str(data.get("storage") or "").lower() == "s3"
        or key.startswith(message_s3_prefix() + "/")
        or s3_key
    ):
        owner = await load_owner_user_by_email(owner_id)
        folder_uuid = await get_or_create_folder_uuid(owner)
        if not key_belongs_to_owner_folder(s3_key=key, folder_uuid=folder_uuid):
            # Still allow if Mongo proves ownership (legacy key layout).
            if not await _caller_owns_message_media(
                owner_id, public_id=public_id or None, s3_key=key
            ):
                raise HTTPException(status_code=403, detail="Not authorized for this media")
        url = presign_message_get_url(
            s3_key=key,
            bucket=str(data.get("s3_bucket") or "").strip() or None,
            expires_in=900,
        )
        if not url:
            raise HTTPException(status_code=400, detail="Could not sign media URL")
        return {
            "public_id": public_id or key,
            "s3_key": key,
            "url": url,
            "storage": "s3",
            "access_mode": "private",
            "url_expires_in": 900,
        }

    if not (
        public_id.startswith(MESSAGE_MEDIA_FOLDER.rstrip("/"))
        or public_id.startswith("letters/media")
    ):
        raise HTTPException(status_code=403, detail="Not authorized for this media")
    if not await _caller_owns_message_media(owner_id, public_id=public_id):
        raise HTTPException(status_code=403, detail="Not authorized for this media")

    resource_type = data.get("resource_type") or data.get("type") or "video"
    try:
        url = signed_media_delivery_url(
            public_id,
            resource_type=str(resource_type),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not sign media URL: {exc}"
        ) from exc

    return {
        "public_id": public_id,
        "url": url,
        "access_mode": "authenticated",
        "url_expires_in": 900,
    }


@router.post("/media")
async def upload_message_media(
    request: Request,
    file: UploadFile = File(...),
    kind: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
):
    owner_email = await resolve_message_owner_id(request, authorization, write=True)
    print(
        f"📤 Message media upload start owner={owner_email!r} "
        f"name={file.filename!r} type={file.content_type!r} kind_hint={kind!r}"
    )

    if not is_allowed_message_media(file):
        raise HTTPException(
            status_code=400,
            detail="Only audio, video, or image files are allowed",
        )

    contents = await file.read()
    size = len(contents)
    if size <= 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        validate_message_media_size(size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    media_kind = classify_message_media_kind(file, kind_hint=kind)

    if not settings.message_s3_active:
        raise HTTPException(
            status_code=503,
            detail=(
                "Media storage is not configured. Set AWS_BUCKET / MESSAGE_S3_* "
                "(and MESSAGE_S3_ENABLED=true if using an IAM role without static keys), "
                "then restart the API."
            ),
        )

    owner = await load_owner_user_by_email(owner_email)
    user_id = str(owner.get("_id"))
    try:
        await vault_quota_check(
            user=owner,
            user_id=user_id,
            incoming_bytes=size,
            owner_email=owner_email,
        )
    except HTTPException:
        raise
    except Exception as exc:
        print("❌ Message media quota check failed:", repr(exc))
        raise HTTPException(
            status_code=500,
            detail="Could not verify storage quota. Please try again.",
        ) from exc

    folder_uuid = await get_or_create_folder_uuid(owner)
    try:
        from app.storage.message_s3 import normalize_message_mime

        uploaded = upload_message_bytes_to_s3(
            contents=contents,
            folder_uuid=folder_uuid,
            mime_type=normalize_message_mime(file.content_type, kind=media_kind),
            original_filename=file.filename,
            kind=media_kind,
        )
    except Exception as exc:
        print("❌ Message S3 upload failed:", repr(exc))
        err_name = type(exc).__name__
        err_text = str(exc)
        if "NoSuchBucket" in err_text or "NoSuchBucket" in err_name:
            detail = "S3 bucket not found. Check AWS_BUCKET / MESSAGE_S3_BUCKET."
        elif "AccessDenied" in err_text or "InvalidAccessKeyId" in err_text:
            detail = (
                "S3 access denied. Check AWS credentials / IAM permissions "
                "for PutObject on the media bucket."
            )
        elif "Could not connect" in err_text or "EndpointConnection" in err_name:
            detail = "Could not reach S3. Check network / AWS region settings."
        else:
            detail = "Could not store media on S3. Please try again."
        raise HTTPException(status_code=500, detail=detail) from exc

    print(
        f"✅ Message media uploaded kind={media_kind} key={uploaded.get('s3_key')!r} "
        f"bytes={size}"
    )
    return {
        "url": uploaded.get("url"),
        "public_id": uploaded.get("public_id"),
        "s3_key": uploaded.get("s3_key"),
        "s3_bucket": uploaded.get("s3_bucket"),
        "storage": "s3",
        "type": uploaded.get("type") or media_kind,
        "format": uploaded.get("format"),
        "size": uploaded.get("size") or size,
        "mime_type": uploaded.get("mime_type"),
        "folder_uuid": folder_uuid,
        "access_mode": "private",
        "url_expires_in": 900,
    }


@router.post("/media/delete")
async def delete_uploaded_message_media(
    payload: MediaDeleteRequest,
    request: Request, authorization: str | None = Header(default=None)
):
    """Delete message media from S3 / Cloudinary.

    Used for:
    - orphan cleanup (uploaded while composing, before the message is saved)
    - removing an attachment that is already linked to one of the owner's messages
    """
    owner_email = await resolve_message_owner_id(request, authorization, write=True)

    public_id = (payload.public_id or "").strip()
    s3_key = (getattr(payload, "s3_key", None) or "").strip()
    key = s3_key or public_id
    if not key:
        raise HTTPException(status_code=400, detail="public_id or s3_key required")

    is_s3 = bool(s3_key) or key.startswith(message_s3_prefix() + "/")

    if is_s3:
        owner = await load_owner_user_by_email(owner_email)
        folder_uuid = await get_or_create_folder_uuid(owner)
        if not key_belongs_to_owner_folder(s3_key=key, folder_uuid=folder_uuid):
            if not await _caller_owns_message_media(
                owner_email, public_id=public_id or None, s3_key=key
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Only your message media can be deleted from this endpoint",
                )
        await messageofnextkin_collection.update_many(
            {
                "owner_id": owner_email,
                "is_deleted": False,
                "$or": [{"media.s3_key": key}, {"media.public_id": key}],
            },
            {"$set": {"media": None, "updated_at": datetime.utcnow()}},
        )
        delete_message_s3_object(s3_key=key)
        return {"status": "deleted", "storage": "s3"}

    if not public_id.startswith(f"{MESSAGE_MEDIA_FOLDER}/") and public_id != MESSAGE_MEDIA_FOLDER:
        raise HTTPException(
            status_code=400,
            detail="Only message media can be deleted from this endpoint",
        )

    await messageofnextkin_collection.update_many(
        {
            "owner_id": owner_email,
            "media.public_id": public_id,
            "is_deleted": False,
        },
        {"$set": {"media": None, "updated_at": datetime.utcnow()}},
    )

    # Allow orphan deletes: media uploaded during compose is not in Mongo yet.
    delete_file(public_id, payload.resource_type)

    return {"status": "deleted", "storage": "cloudinary"}
