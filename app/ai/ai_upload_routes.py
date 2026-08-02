# app/ai/ai_upload_routes.py

import hashlib
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

from app.config import settings
from app.database import ai_documents_collection
from app.ai.ai_auth import get_current_owner, get_user_id
from app.ai.local_document_extract import extract_document_text
from app.storage.vault import (
    ensure_owner_vault_dir,
    resolve_stored_ai_document_path,
    resolve_vault_file_path,
    user_quota_bytes,
    vault_quota_check,
    vault_usage_bytes,
)


router = APIRouter(prefix="/ai", tags=["ai-upload"])

# 0 = permanent vault storage (recommended on VPS).
AI_UPLOAD_TTL_MINUTES = int(
    os.getenv("AI_UPLOAD_TTL_MINUTES", str(settings.AI_UPLOAD_TTL_MINUTES))
)
MAX_FILE_SIZE = settings.AI_UPLOAD_MAX_BYTES

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


def content_hash_for_bytes(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def _cache_is_reusable(doc: dict | None) -> bool:
    if not isinstance(doc, dict):
        return False
    cached = doc.get("cached_extractions")
    if isinstance(cached, dict) and cached:
        return True
    classification = doc.get("last_classification")
    return isinstance(classification, dict) and bool(classification)


async def find_reusable_hash_match(*, user_id: str, content_hash: str) -> dict | None:
    """Prior upload of the exact same bytes with reusable AI cache/classification."""
    if not content_hash:
        return None
    cursor = (
        ai_documents_collection.find(
            {
                "user_id": user_id,
                "content_hash": content_hash,
            }
        )
        .sort("created_at", -1)
        .limit(8)
    )
    async for doc in cursor:
        if _cache_is_reusable(doc):
            return doc
    return None


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
        "content_hash": doc.get("content_hash"),
        "extract_reuse": bool(doc.get("extract_reuse")),
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
    if AI_UPLOAD_TTL_MINUTES <= 0:
        return

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
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Max {int(settings.AI_UPLOAD_MAX_MB)}MB.",
        )

    await vault_quota_check(
        user=current_user,
        user_id=user_id,
        incoming_bytes=len(contents),
    )

    ext = ALLOWED_MIME_TYPES[file.content_type]
    file_id = uuid.uuid4().hex
    stored_filename = f"{file_id}{ext}"
    folder_uuid, vault_dir = await ensure_owner_vault_dir(current_user)
    file_path = resolve_vault_file_path(folder_uuid, stored_filename)
    original_filename = (file.filename or f"document{ext}").strip() or f"document{ext}"
    section_key = str(section).strip() if section is not None else ""
    content_hash = content_hash_for_bytes(contents)

    try:
        # Exact byte match — copy AI cache before topic cleanup deletes the prior row.
        prior = await find_reusable_hash_match(
            user_id=user_id,
            content_hash=content_hash,
        )
        extract_reuse = bool(prior)
        reused_from_file_id = str(prior.get("_id")) if prior else None

        # Same topic re-upload: delete previous DB row + disk file, then add new.
        replaced_file_ids = await delete_same_topic_documents(
            user_id=user_id,
            original_filename=original_filename,
            section=section_key or None,
        )

        vault_dir.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(contents)

        # Best effort file permission hardening.
        try:
            os.chmod(file_path, 0o600)
        except Exception:
            pass

        now = utc_now_naive()
        expires_at = (
            now + timedelta(minutes=AI_UPLOAD_TTL_MINUTES)
            if AI_UPLOAD_TTL_MINUTES > 0
            else None
        )

        # Local extract snapshot (cheap for TXT/searchable PDF; OCR optional).
        local_extract = extract_document_text(file_path, file.content_type)

        doc = {
            "_id": file_id,
            "user_id": user_id,
            "owner_id": user_id,
            "folder_uuid": folder_uuid,
            "role": "owner",
            "path": str(file_path),
            "stored_filename": stored_filename,
            "original_filename": original_filename,
            "mime_type": file.content_type,
            "size_bytes": len(contents),
            "content_hash": content_hash,
            "created_at": now,
            "updated_at": now,
            "expires_at": expires_at,
            "status": "uploaded",
            "source": "upload",
            "storage": "vault",
            "extracted_text": (local_extract.get("text") or "")[:50000],
            "extract_method": local_extract.get("method"),
            "extract_quality": local_extract.get("quality_score"),
            "needs_vision": bool(local_extract.get("needs_vision")),
            "extract_reuse": extract_reuse,
            "unchanged": extract_reuse,
        }
        if section_key:
            doc["section"] = section_key

        if prior:
            for key in (
                "cached_extractions",
                "last_classification",
                "routed_section",
                "pending_sections",
                "document_summary",
            ):
                if prior.get(key) is not None:
                    doc[key] = prior.get(key)
            if reused_from_file_id:
                doc["reused_from_file_id"] = reused_from_file_id

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
            "expires_at": expires_at.isoformat() if expires_at else None,
            "preview_url": f"/ai/document/{file_id}/preview",
            "content_hash": content_hash,
            "unchanged": extract_reuse,
            "extract_reuse": extract_reuse,
            "reused_from_file_id": reused_from_file_id,
            "needs_vision": bool(local_extract.get("needs_vision")),
            "extract_method": local_extract.get("method"),
            "extract_quality": local_extract.get("quality_score"),
            "storage": "vault",
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
                {"expires_at": {"$exists": False}},
            ],
        }
    ).sort("created_at", -1)

    documents = []
    async for doc in cursor:
        path = resolve_stored_ai_document_path(doc)
        if not path:
            continue
        documents.append(serialize_ai_document(doc))

    used = await vault_usage_bytes(user_id=user_id)
    return {
        "success": True,
        "documents": documents,
        "storage": {
            "used_bytes": used,
            "user_quota_bytes": user_quota_bytes(current_user),
            "global_quota_bytes": settings.VAULT_GLOBAL_QUOTA_BYTES,
        },
    }


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

    path = resolve_stored_ai_document_path(doc)
    if not path:
        # Content-hash reuse may point at an earlier upload that still has bytes.
        reused_id = doc.get("reused_from_file_id")
        if reused_id and str(reused_id) != str(file_id):
            prior = await ai_documents_collection.find_one(
                {"_id": reused_id, "user_id": user_id},
            )
            if prior:
                path = resolve_stored_ai_document_path(prior)
                if path:
                    doc = prior
    if not path:
        raise HTTPException(
            status_code=404,
            detail="Document file missing on disk. Re-upload the file to preview it.",
        )

    filename = doc.get("original_filename") or path.name
    media_type = (doc.get("mime_type") or "").strip() or "application/octet-stream"

    # Some uploads land as octet-stream / empty — sniff from extension so
    # image + text previews work in the browser.
    if media_type in {"", "application/octet-stream", "binary/octet-stream"}:
        lower = str(filename).lower()
        if lower.endswith(".pdf"):
            media_type = "application/pdf"
        elif lower.endswith(".png"):
            media_type = "image/png"
        elif lower.endswith((".jpg", ".jpeg")):
            media_type = "image/jpeg"
        elif lower.endswith(".webp"):
            media_type = "image/webp"
        elif lower.endswith(".gif"):
            media_type = "image/gif"
        elif lower.endswith((".tif", ".tiff")):
            media_type = "image/tiff"
        elif lower.endswith(".bmp"):
            media_type = "image/bmp"
        elif lower.endswith((".txt", ".csv", ".md", ".log")):
            media_type = "text/plain"
        elif lower.endswith(".json"):
            media_type = "application/json"

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
    """Owner deletes a vault upload (disk + Mongo) from overview / section history."""
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
