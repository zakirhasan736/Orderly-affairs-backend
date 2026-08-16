# app/ai/ai_upload_routes.py

import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, Response

from app.config import settings
from app.database import ai_documents_collection
from app.ai.ai_auth import get_user_id, get_vault_owner_for_ai
from app.ai.document_topic import (
    delete_matching_topic_documents,
    fingerprint_from_parts,
)
from app.ai.ai_document_storage import (
    destroy_ai_document_assets,
    fetch_ai_document_bytes,
    write_temp_ai_file,
    upload_ai_bytes_to_storage,
)
from app.ai.ai_extract_crypto import encrypt_extracted_text
from app.ai.local_document_extract import prepare_document_for_sol
from app.storage.vault import (
    get_or_create_folder_uuid,
    recover_ai_document_path,
    vault_quota_check,
)
from app.security.document_guard import DocumentGuardError, guard_upload
from app.security.malware_scan import MalwareScanError, sniff_payload_kind


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/ai", tags=["ai-upload"])


async def ensure_ai_documents_list_index() -> None:
    """Keep GET /ai/documents fast: user_id + created_at, no full-document scan."""
    await ai_documents_collection.create_index(
        [("user_id", 1), ("created_at", -1)],
        name="ai_docs_user_created",
        background=True,
    )

# 0 = permanent storage (S3 / Cloudinary + Mongo).
AI_UPLOAD_TTL_MINUTES = int(
    os.getenv("AI_UPLOAD_TTL_MINUTES", str(settings.AI_UPLOAD_TTL_MINUTES))
)
MAX_FILE_SIZE = settings.AI_UPLOAD_MAX_BYTES

ALLOWED_MIME_TYPES = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/webp": ".webp",
}

_MIME_ALIASES = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "application/x-pdf": "application/pdf",
}

_KIND_TO_MIME = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "text": "text/plain",
}


def _normalize_upload_mime(content_type: str | None, filename: str | None) -> str:
    claimed = (content_type or "").split(";")[0].strip().lower()
    claimed = _MIME_ALIASES.get(claimed, claimed)
    if claimed in ALLOWED_MIME_TYPES:
        return claimed
    name = (filename or "").strip().lower()
    if name.endswith(".pdf"):
        return "application/pdf"
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".jpg") or name.endswith(".jpeg"):
        return "image/jpeg"
    if name.endswith(".webp"):
        return "image/webp"
    if name.endswith(".txt"):
        return "text/plain"
    return claimed


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


def normalize_document_topic(name: Optional[str]) -> str:
    """
    Collapse common re-download / copy suffixes so Auto_Insurance.pdf and
    Auto_Insurance (1).pdf count as the same replaceable topic.
    """
    raw = str(name or "").strip().lower()
    # Drop extension for matching (pdf vs PNG of same stem still collide —
    # prefer stem identity for "same document" replace).
    stem = re.sub(r"\.[a-z0-9]{1,8}$", "", raw)
    stem = re.sub(r"[\s._-]*\(\d+\)$", "", stem)
    stem = re.sub(r"[\s._-]*(copy|副本)$", "", stem)
    stem = re.sub(r"[\s._-]+$", "", stem)
    return " ".join(stem.replace("_", " ").replace("-", " ").split())


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
    raw_status = str(doc.get("status") or "uploaded").strip().lower()
    consumed = doc.get("consumed_sections") or []
    cached = doc.get("cached_extractions") or {}
    has_fill = (isinstance(consumed, list) and len(consumed) > 0) or (
        isinstance(cached, dict) and bool(cached)
    ) or raw_status in {"ready", "done", "complete", "filled"}
    # Autofill success historically reset status to "uploaded" / left "processing".
    # Surface those as ready once the document has already filled vault fields.
    if has_fill and raw_status in {"uploaded", "processing", "extracting", "classifying", "queued"}:
        ui_status = "ready"
    elif raw_status in {"ready", "done", "complete", "filled"}:
        ui_status = "ready"
    else:
        ui_status = raw_status or "uploaded"

    return {
        "file_id": str(doc.get("_id")),
        "name": doc.get("original_filename") or doc.get("stored_filename") or "Document",
        "original_filename": doc.get("original_filename"),
        "mime_type": doc.get("mime_type"),
        "size_bytes": doc.get("size_bytes"),
        "status": ui_status,
        "filled": bool(has_fill),
        "consumed_sections": list(consumed) if isinstance(consumed, list) else [],
        "created_at": created.isoformat() if created else None,
        "updated_at": updated.isoformat() if updated else None,
        "expires_at": expires.isoformat() if expires else None,
        "preview_url": f"/ai/document/{doc.get('_id')}/preview",
        "source": doc.get("source") or "upload",
        "section": doc.get("routed_section") or doc.get("section"),
        "pending_sections": (
            list(doc.get("pending_sections") or [])
            if isinstance(doc.get("pending_sections"), list)
            else []
        ),
        "content_hash": doc.get("content_hash"),
        "extract_reuse": bool(doc.get("extract_reuse")),
        "storage": doc.get("storage") or "vault",
        "public_id": doc.get("public_id"),
        "s3_key": doc.get("s3_key"),
    }


def sniff_media_type(filename: str, media_type: str, sample: bytes | None = None) -> str:
    media_type = (media_type or "").strip() or "application/octet-stream"
    lower = str(filename).lower()

    if sample:
        if sample.startswith(b"%PDF"):
            return "application/pdf"
        if sample.startswith(b"\x89PNG"):
            return "image/png"
        if sample[:3] == b"\xff\xd8\xff":
            return "image/jpeg"

    if media_type not in {"", "application/octet-stream", "binary/octet-stream"}:
        return media_type
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith((".tif", ".tiff")):
        return "image/tiff"
    if lower.endswith(".bmp"):
        return "image/bmp"
    if lower.endswith((".txt", ".csv", ".md", ".log")):
        return "text/plain"
    if lower.endswith(".json"):
        return "application/json"

    if not sample:
        return media_type

    head = sample[:16]
    if sample.startswith(b"%PDF"):
        return "application/pdf"
    if sample.startswith(b"\x89PNG"):
        return "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if sample.startswith(b"GIF8"):
        return "image/gif"
    if len(sample) >= 12 and sample[:4] == b"RIFF" and sample[8:12] == b"WEBP":
        return "image/webp"
    if head[:2] == b"BM":
        return "image/bmp"
    if sample and b"\x00" not in sample and all(
        b in (9, 10, 13) or 32 <= b <= 126 or b >= 128 for b in sample
    ):
        return "text/plain"
    return media_type


async def delete_same_topic_documents(
    *,
    user_id: str,
    original_filename: str,
    section: Optional[str] = None,
    content_hash: Optional[str] = None,
    keep_file_id: Optional[str] = None,
    classification: Optional[dict] = None,
    extractions: Optional[dict] = None,
    extra_text: Optional[str] = None,
) -> List[str]:
    """
    Replace prior uploads of the same topic for this owner.
    Match by filename stem, Jeep-insurance-style kind+vehicle, or exact bytes.
    Deletes S3/Cloudinary + Mongo.
    """
    incoming = fingerprint_from_parts(
        filename=original_filename,
        summary=str((classification or {}).get("document_summary") or ""),
        section_key=str(
            (classification or {}).get("best_section_key") or section or ""
        ),
        extra_text=extra_text,
        fields=extractions,
    )
    return await delete_matching_topic_documents(
        user_id=user_id,
        incoming=incoming,
        keep_file_id=keep_file_id,
        section=section,
        content_hash=content_hash,
    )


async def cleanup_expired_ai_documents():
    if AI_UPLOAD_TTL_MINUTES <= 0:
        return

    now = utc_now_naive()

    cursor = ai_documents_collection.find(
        {"expires_at": {"$lte": now}},
        {"path": 1, "public_id": 1, "resource_type": 1},
    )

    async for doc in cursor:
        destroy_ai_document_assets(doc)
        await ai_documents_collection.delete_one({"_id": doc["_id"]})


@router.post("/upload-document")
async def upload_ai_document(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    section: Optional[str] = Form(None),
    authorization: str | None = Header(default=None),
):
    background_tasks.add_task(cleanup_expired_ai_documents)

    current_user = await get_vault_owner_for_ai(
        request, authorization, require_upload=True
    )
    user_id = get_user_id(current_user)

    contents = await file.read()

    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Max {int(settings.AI_UPLOAD_MAX_MB)}MB.",
        )

    kind = sniff_payload_kind(contents)
    if kind == "heic":
        raise HTTPException(
            status_code=400,
            detail=(
                "iPhone HEIC photos are not supported. "
                "Save or export as JPG or PDF, then upload again."
            ),
        )

    upload_mime = _normalize_upload_mime(file.content_type, file.filename)
    if upload_mime not in ALLOWED_MIME_TYPES:
        upload_mime = _KIND_TO_MIME.get(kind, upload_mime)
    if upload_mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Upload PDF, TXT, PNG, JPG, JPEG, or WEBP.",
        )

    try:
        guarded = guard_upload(
            contents,
            mime_type=upload_mime,
            filename=file.filename,
        )
    except (MalwareScanError, DocumentGuardError) as exc:
        logger.warning(
            "AI upload blocked file=%s mime=%s reason=%s",
            file.filename,
            file.content_type,
            exc,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    original_hash = content_hash_for_bytes(contents)
    contents = guarded.payload
    stored_mime = (
        guarded.mime_type
        if guarded.mime_type in ALLOWED_MIME_TYPES
        else file.content_type
    )
    scan = guarded.scan

    await vault_quota_check(
        user=current_user,
        user_id=user_id,
        incoming_bytes=len(contents),
    )

    ext = ALLOWED_MIME_TYPES[stored_mime]
    file_id = uuid.uuid4().hex
    stored_filename = f"{file_id}{ext}"
    original_filename = (file.filename or f"document{ext}").strip() or f"document{ext}"
    section_key = str(section).strip() if section is not None else ""
    content_hash = original_hash
    email = str(current_user.get("email") or "").strip().lower()
    folder_uuid = await get_or_create_folder_uuid(current_user)
    temp_path: Path | None = None
    storage_meta: dict | None = None

    try:
        # Exact byte match — copy AI cache before topic cleanup deletes the prior row.
        prior = await find_reusable_hash_match(
            user_id=user_id,
            content_hash=content_hash,
        )
        extract_reuse = bool(prior)
        reused_from_file_id = str(prior.get("_id")) if prior else None

        # Same topic / same bytes re-upload: delete previous remote bytes + DB row.
        replaced_file_ids = await delete_same_topic_documents(
            user_id=user_id,
            original_filename=original_filename,
            section=section_key or None,
            content_hash=content_hash,
        )

        # Scan already ran above. Read path: OCR first, Terra only on bad pages,
        # then store prepared text for Sol mapping (never send original bytes to Sol).
        temp_path = write_temp_ai_file(contents, ext)
        if extract_reuse and prior:
            local_extract = {
                "text": "",
                "method": prior.get("extract_method") or "ocr",
                "quality_score": prior.get("extract_quality"),
                "needs_vision": bool(prior.get("needs_vision")),
                "quality": prior.get("extract_quality_label") or "good",
                "terra_invoked": bool(prior.get("terra_invoked")),
                "terra_pages": prior.get("terra_pages") or [],
                "pipeline_path": prior.get("pipeline_path")
                or ("ocr_terra_sol" if prior.get("terra_invoked") else "ocr_sol"),
            }
        else:
            local_extract = prepare_document_for_sol(temp_path, stored_mime)

        storage_meta = upload_ai_bytes_to_storage(
            contents=contents,
            folder_uuid=folder_uuid,
            stored_filename=stored_filename,
            mime_type=stored_mime,
            original_filename=original_filename,
            email=email,
            user_id=user_id,
        )
        storage_kind = str(storage_meta.get("storage") or "")
        if storage_kind == "s3" and not storage_meta.get("s3_key"):
            raise HTTPException(
                status_code=500,
                detail="Document upload to S3 failed.",
            )
        if storage_kind == "cloudinary" and (
            not storage_meta.get("public_id") or not storage_meta.get("secure_url")
        ):
            raise HTTPException(
                status_code=500,
                detail="Document upload to Cloudinary failed.",
            )

        now = utc_now_naive()
        expires_at = (
            now + timedelta(minutes=AI_UPLOAD_TTL_MINUTES)
            if AI_UPLOAD_TTL_MINUTES > 0
            else None
        )

        prepared_text = str(
            local_extract.get("document_text") or local_extract.get("text") or ""
        )[:50000]
        terra_invoked = bool(local_extract.get("terra_invoked"))
        terra_pages = local_extract.get("terra_pages") or []
        pipeline_path = str(
            local_extract.get("pipeline_path")
            or ("ocr_terra_sol" if terra_invoked else "ocr_sol")
        )
        if extract_reuse and prior and prior.get("extracted_text"):
            extracted_blob = prior.get("extracted_text")
        else:
            extracted_blob = encrypt_extracted_text(
                user_id=user_id,
                file_id=file_id,
                text=prepared_text,
            )

        doc = {
            "_id": file_id,
            "user_id": user_id,
            "owner_id": user_id,
            "role": "owner",
            "folder_uuid": folder_uuid,
            "stored_filename": stored_filename,
            "original_filename": original_filename,
            "mime_type": stored_mime,
            "size_bytes": len(contents),
            "content_hash": content_hash,
            "created_at": now,
            "updated_at": now,
            "expires_at": expires_at,
            "status": "uploaded",
            "source": "upload",
            "scan_status": scan.status,
            "scan_engine": scan.engine,
            "scan_sanitized": guarded.sanitized,
            "extracted_text": extracted_blob,
            "extract_method": local_extract.get("method")
            or local_extract.get("source_method")
            or "ocr",
            "extract_quality": local_extract.get("quality_score"),
            "needs_vision": bool(local_extract.get("needs_vision")) and not terra_invoked,
            "terra_invoked": terra_invoked,
            "terra_pages": terra_pages,
            "pipeline_path": pipeline_path,
            "extract_reuse": extract_reuse,
            "unchanged": extract_reuse,
            **{
                k: v
                for k, v in storage_meta.items()
                if k
                in {
                    "storage",
                    "s3_bucket",
                    "s3_key",
                    "s3_region",
                    "public_id",
                    "secure_url",
                    "resource_type",
                    "cloudinary_folder",
                }
                and v is not None
            },
        }
        if section_key:
            doc["section"] = section_key
        doc["topic_fingerprint"] = fingerprint_from_parts(
            filename=original_filename,
            section_key=section_key or None,
        )

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
            "mime_type": stored_mime,
            "size_bytes": len(contents),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "preview_url": f"/ai/document/{file_id}/preview",
            "content_hash": content_hash,
            "unchanged": extract_reuse,
            "extract_reuse": extract_reuse,
            "reused_from_file_id": reused_from_file_id,
            "needs_vision": bool(doc.get("needs_vision")),
            "terra_invoked": terra_invoked,
            "terra_pages": terra_pages,
            "pipeline_path": pipeline_path,
            "extract_method": doc.get("extract_method"),
            "extract_quality": local_extract.get("quality_score"),
            "storage": storage_kind,
            "s3_key": storage_meta.get("s3_key"),
            "public_id": storage_meta.get("public_id"),
            "scan_status": scan.status,
            "scan_engine": scan.engine,
            "scan_sanitized": guarded.sanitized,
        }

    except HTTPException:
        if storage_meta:
            destroy_ai_document_assets(storage_meta)
        raise

    except Exception as e:
        print("❌ AI document upload failed:", repr(e))
        if storage_meta:
            destroy_ai_document_assets(storage_meta)
        raise HTTPException(status_code=500, detail="Document upload failed.")

    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


@router.get("/documents")
async def list_owner_ai_documents(
    background_tasks: BackgroundTasks,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """List vault owner's uploaded autofill documents (owner + family readers)."""
    background_tasks.add_task(cleanup_expired_ai_documents)
    current_user = await get_vault_owner_for_ai(request, authorization)
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
        },
        {
            "extracted_text": 0,
            "cached_extractions": 0,
        },
    ).sort("created_at", -1).limit(200)

    documents = []
    async for doc in cursor:
        storage = str(doc.get("storage") or "").lower()
        if doc.get("s3_key") or storage == "s3":
            documents.append(serialize_ai_document(doc))
            continue
        if doc.get("secure_url") or doc.get("public_id") or storage == "cloudinary":
            documents.append(serialize_ai_document(doc))
            continue
        if doc.get("path") or doc.get("stored_filename"):
            documents.append(serialize_ai_document(doc))

    return {
        "success": True,
        "documents": documents,
    }


@router.get("/document/{file_id}/preview")
async def preview_ai_document(
    file_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Stream the uploaded file so owner/family can view image / text / PDF."""
    current_user = await get_vault_owner_for_ai(request, authorization)
    user_id = get_user_id(current_user)
    doc = await ai_documents_collection.find_one(
        {"_id": file_id, "user_id": user_id},
    )

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    expires_at = normalize_mongo_datetime(doc.get("expires_at"))
    if expires_at and expires_at <= utc_now_naive():
        destroy_ai_document_assets(doc)
        await ai_documents_collection.delete_one({"_id": file_id, "user_id": user_id})
        raise HTTPException(status_code=410, detail="Document expired.")

    filename = doc.get("original_filename") or doc.get("stored_filename") or "document"
    media_type = (doc.get("mime_type") or "").strip() or "application/octet-stream"

    storage = str(doc.get("storage") or "").lower()
    s3_key = str(doc.get("s3_key") or "").strip()
    secure_url = str(doc.get("secure_url") or "").strip()
    public_id = str(doc.get("public_id") or "").strip()

    if storage == "s3" or s3_key or public_id or secure_url:
        try:
            payload = fetch_ai_document_bytes(doc)
        except Exception as exc:
            print(f"❌ Document preview fetch failed for {file_id}: {exc}")
            raise HTTPException(
                status_code=410,
                detail=(
                    "Document file is not available. "
                    "Re-upload the file to preview it."
                ),
            ) from exc

        media_type = sniff_media_type(str(filename), media_type, payload[:512])
        return Response(
            content=payload,
            media_type=media_type,
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Cache-Control": "private, max-age=60",
            },
        )

    path = await recover_ai_document_path(doc)
    if not path:
        raise HTTPException(
            status_code=410,
            detail=(
                "Document file is not available on this server. "
                "Re-upload the image or text file to preview it here."
            ),
        )

    sample = None
    try:
        with open(path, "rb") as fh:
            sample = fh.read(512)
    except Exception:
        sample = None
    media_type = sniff_media_type(str(filename), media_type, sample)

    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=filename,
        content_disposition_type="inline",
    )


@router.delete("/document/{file_id}")
async def delete_uploaded_ai_document(
    file_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Owner or family Editor+ deletes an upload from overview / section history."""
    current_user = await get_vault_owner_for_ai(
        request, authorization, require_upload=True
    )
    user_id = get_user_id(current_user)
    doc = await ai_documents_collection.find_one(
        {"_id": file_id, "user_id": user_id},
        {
            "path": 1,
            "public_id": 1,
            "resource_type": 1,
            "storage": 1,
            "s3_key": 1,
            "s3_bucket": 1,
        },
    )

    if not doc:
        # Already gone — treat as success for idempotent cleanup.
        return {"success": True, "deleted": False}

    destroy_ai_document_assets(doc)
    await ai_documents_collection.delete_one({"_id": file_id, "user_id": user_id})
    return {"success": True, "deleted": True}
