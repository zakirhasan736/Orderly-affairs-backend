# app/ai/ai_upload_routes.py

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, BackgroundTasks

from app.database import ai_documents_collection
from app.ai.ai_auth import get_current_owner, get_user_id


router = APIRouter(prefix="/ai", tags=["ai-upload"])

UPLOAD_DIR = Path(os.getenv("AI_UPLOAD_DIR", "app/uploads/ai-documents"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

AI_UPLOAD_TTL_MINUTES = int(os.getenv("AI_UPLOAD_TTL_MINUTES", "30"))
MAX_FILE_SIZE = 15 * 1024 * 1024  # 15MB

ALLOWED_MIME_TYPES = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


def utc_now_naive():
    """
    MongoDB/Motor usually returns datetime as timezone-naive UTC.
    So we store naive UTC also, to avoid:
    TypeError: can't compare offset-naive and offset-aware datetimes
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_mongo_datetime(value):
    if not value:
        return None

    if getattr(value, "tzinfo", None) is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    return value


def safe_delete_file(path_value: str | None):
    if not path_value:
        return

    try:
        path = Path(path_value)

        if path.exists() and path.is_file():
            path.unlink()
    except Exception:
        pass


async def cleanup_expired_ai_documents():
    now = utc_now_naive()

    cursor = ai_documents_collection.find(
        {"expires_at": {"$lte": now}},
        {"path": 1},
    )

    async for doc in cursor:
        safe_delete_file(doc.get("path"))
        await ai_documents_collection.delete_one({"_id": doc["_id"]})


@router.post("/upload-document")
async def upload_ai_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user=Depends(get_current_owner),
):
    background_tasks.add_task(cleanup_expired_ai_documents)

    user_id = get_user_id(current_user)

    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Upload PDF, TXT, PNG, JPG, JPEG, or WEBP.",
        )

    contents = await file.read()

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max 15MB.")

    ext = ALLOWED_MIME_TYPES[file.content_type]
    file_id = uuid.uuid4().hex
    stored_filename = f"{file_id}{ext}"
    file_path = UPLOAD_DIR / stored_filename

    try:
        file_path.write_bytes(contents)

        # Best effort file permission hardening.
        try:
            os.chmod(file_path, 0o600)
        except Exception:
            pass

        now = utc_now_naive()
        expires_at = now + timedelta(minutes=AI_UPLOAD_TTL_MINUTES)

        await ai_documents_collection.insert_one(
            {
                "_id": file_id,
                "user_id": user_id,
                "path": str(file_path),
                "stored_filename": stored_filename,
                "original_filename": file.filename,
                "mime_type": file.content_type,
                "size_bytes": len(contents),
                "created_at": now,
                "expires_at": expires_at,
                "status": "uploaded",
            }
        )

        return {
            "success": True,
            "file_id": file_id,
            "mime_type": file.content_type,
            "expires_at": expires_at.isoformat(),
        }

    except HTTPException:
        safe_delete_file(str(file_path))
        raise

    except Exception as e:
        print("❌ AI document upload failed:", repr(e))
        safe_delete_file(str(file_path))
        raise HTTPException(status_code=500, detail="Document upload failed.")


@router.delete("/document/{file_id}")
async def delete_uploaded_ai_document(
    file_id: str,
    current_user=Depends(get_current_owner),
):
    """Owner deletes a temporary AI upload (disk + Mongo) after fill is done."""
    user_id = get_user_id(current_user)
    doc = await ai_documents_collection.find_one(
        {"_id": file_id, "user_id": user_id},
        {"path": 1},
    )

    if not doc:
        # Already gone — treat as success for idempotent cleanup.
        return {"success": True, "deleted": False}

    safe_delete_file(doc.get("path"))
    await ai_documents_collection.delete_one({"_id": file_id, "user_id": user_id})
    return {"success": True, "deleted": True}