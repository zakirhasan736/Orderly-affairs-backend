# app/ai/ai_upload_routes.py

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse

from app.database import ai_documents_collection
from app.ai.ai_auth import get_current_owner, get_user_id


router = APIRouter(prefix="/ai", tags=["ai-upload"])

UPLOAD_DIR = Path(os.getenv("AI_UPLOAD_DIR", "app/uploads/ai-documents"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Keep owner uploads available long enough to re-open / preview after autofill.
AI_UPLOAD_TTL_MINUTES = int(os.getenv("AI_UPLOAD_TTL_MINUTES", str(7 * 24 * 60)))
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


def normalize_document_topic(name: Optional[str]) -> str:
    return " ".join(str(name or "").strip().lower().split())


def serialize_ai_document(doc: dict) -> dict:
    created = normalize_mongo_datetime(doc.get("created_at"))
    updated = normalize_mongo_datetime(doc.get("updated_at")) or created
    expires = normalize_mongo_datetime(doc.get("expires_at"))
    return {
        "file_id": str(doc.get("_id")),
        "name": doc.get("original_filename") or doc.get("stored_filename") or "Document",
        "original_filename": doc.get("original_filename"),
        "mime_type": doc.get("mime_type"),
        "size_bytes": doc.get("size_bytes"),
        "status": doc.get("status") or "uploaded",
        "created_at": created.isoformat() if created else None,
        "updated_at": updated.isoformat() if updated else None,
        "expires_at": expires.isoformat() if expires else None,
        "preview_url": f"/ai/document/{doc.get('_id')}/preview",
        "source": doc.get("source") or "upload",
        "section": doc.get("routed_section") or doc.get("section"),
    }


async def delete_same_topic_documents(
    *,
    user_id: str,
    original_filename: str,
    section: Optional[str] = None,
) -> List[str]:
    """
    Replace prior uploads of the same document topic for this owner.
    Same topic = same normalized filename (+ matching section when provided).
    """
    topic = normalize_document_topic(original_filename)
    if not topic:
        return []

    section_key = str(section).strip() if section is not None else ""
    replaced: List[str] = []

    cursor = ai_documents_collection.find(
        {"user_id": user_id},
        {
            "_id": 1,
            "path": 1,
            "original_filename": 1,
            "section": 1,
            "routed_section": 1,
        },
    )

    async for doc in cursor:
        if normalize_document_topic(doc.get("original_filename")) != topic:
            continue

        if section_key:
            doc_section = str(
                doc.get("section") or doc.get("routed_section") or ""
            ).strip()
            # Keep other sections' copies; replace overview/unscoped + same section.
            if doc_section and doc_section not in (section_key, "overview"):
                continue

        file_id = str(doc.get("_id") or "")
        if not file_id:
            continue

        safe_delete_file(doc.get("path"))
        await ai_documents_collection.delete_one({"_id": doc["_id"]})
        replaced.append(file_id)

    return replaced


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
    section: Optional[str] = Form(None),
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
    original_filename = (file.filename or f"document{ext}").strip() or f"document{ext}"
    section_key = str(section).strip() if section is not None else ""

    try:
        # Same topic re-upload: delete previous DB row + disk file, then add new.
        replaced_file_ids = await delete_same_topic_documents(
            user_id=user_id,
            original_filename=original_filename,
            section=section_key or None,
        )

        file_path.write_bytes(contents)

        # Best effort file permission hardening.
        try:
            os.chmod(file_path, 0o600)
        except Exception:
            pass

        now = utc_now_naive()
        expires_at = now + timedelta(minutes=AI_UPLOAD_TTL_MINUTES)

        doc = {
            "_id": file_id,
            "user_id": user_id,
            "owner_id": user_id,
            "role": "owner",
            "path": str(file_path),
            "stored_filename": stored_filename,
            "original_filename": original_filename,
            "mime_type": file.content_type,
            "size_bytes": len(contents),
            "created_at": now,
            "updated_at": now,
            "expires_at": expires_at,
            "status": "uploaded",
            "source": "upload",
        }
        if section_key:
            doc["section"] = section_key

        await ai_documents_collection.insert_one(doc)

        return {
            "success": True,
            "file_id": file_id,
            "name": original_filename,
            "updated_at": now.isoformat(),
            "replaced_file_ids": replaced_file_ids,
            "replaced": bool(replaced_file_ids),
            "original_filename": original_filename,
            "mime_type": file.content_type,
            "size_bytes": len(contents),
            "expires_at": expires_at.isoformat(),
            "preview_url": f"/ai/document/{file_id}/preview",
        }

    except HTTPException:
        safe_delete_file(str(file_path))
        raise

    except Exception as e:
        print("❌ AI document upload failed:", repr(e))
        safe_delete_file(str(file_path))
        raise HTTPException(status_code=500, detail="Document upload failed.")


@router.get("/documents")
async def list_owner_ai_documents(
    background_tasks: BackgroundTasks,
    current_user=Depends(get_current_owner),
):
    """List this owner's uploaded autofill documents (name + metadata)."""
    background_tasks.add_task(cleanup_expired_ai_documents)
    user_id = get_user_id(current_user)
    now = utc_now_naive()

    cursor = ai_documents_collection.find(
        {
            "user_id": user_id,
            "$or": [
                {"expires_at": {"$gt": now}},
                {"expires_at": None},
            ],
        }
    ).sort("created_at", -1)

    documents = []
    async for doc in cursor:
        path = Path(doc.get("path") or "")
        if not path.exists():
            continue
        documents.append(serialize_ai_document(doc))

    return {"success": True, "documents": documents}


@router.get("/document/{file_id}/preview")
async def preview_ai_document(
    file_id: str,
    current_user=Depends(get_current_owner),
):
    """Stream the uploaded file so the owner can view image / text / PDF."""
    user_id = get_user_id(current_user)
    doc = await ai_documents_collection.find_one(
        {"_id": file_id, "user_id": user_id},
    )

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    expires_at = normalize_mongo_datetime(doc.get("expires_at"))
    if expires_at and expires_at <= utc_now_naive():
        safe_delete_file(doc.get("path"))
        await ai_documents_collection.delete_one({"_id": file_id, "user_id": user_id})
        raise HTTPException(status_code=410, detail="Document expired.")

    path = Path(doc.get("path") or "")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Document file missing.")

    filename = doc.get("original_filename") or path.name
    media_type = doc.get("mime_type") or "application/octet-stream"

    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=filename,
        content_disposition_type="inline",
    )


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
