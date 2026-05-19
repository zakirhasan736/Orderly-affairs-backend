# app/ai/ai_autofill_routes.py

import traceback
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from app.ai.autofill_registry import SECTION_EXTRACTORS
from app.database import ai_documents_collection
from app.ai.ai_auth import get_current_owner, get_user_id


router = APIRouter(prefix="/ai", tags=["ai-autofill"])


class AutofillSectionRequest(BaseModel):
    section: str
    file_id: str = Field(..., min_length=16, max_length=100)
    subsection: str | None = None


def utc_now_naive():
    """
    MongoDB/Motor usually returns datetime as timezone-naive UTC.
    So compare with timezone-naive UTC.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_mongo_datetime(value):
    """
    Handles both old timezone-aware values and normal MongoDB timezone-naive values.
    """
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


async def delete_ai_document(file_id: str, user_id: str):
    doc = await ai_documents_collection.find_one(
        {"_id": file_id, "user_id": user_id},
        {"path": 1},
    )

    if doc:
        safe_delete_file(doc.get("path"))

    await ai_documents_collection.delete_one(
        {"_id": file_id, "user_id": user_id}
    )


@router.post("/autofill-section")
async def autofill_section(
    payload: AutofillSectionRequest,
    current_user=Depends(get_current_owner),
):
    user_id = get_user_id(current_user)

    extractor = SECTION_EXTRACTORS.get(payload.section)

    if not extractor:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported section: {payload.section}",
        )

    doc = await ai_documents_collection.find_one(
        {
            "_id": payload.file_id,
            "user_id": user_id,
            "status": "uploaded",
        }
    )

    if not doc:
        raise HTTPException(
            status_code=404,
            detail="Document not found, expired, or already processed.",
        )

    file_path = doc.get("path")
    mime_type = doc.get("mime_type")

    try:
        expires_at = normalize_mongo_datetime(doc.get("expires_at"))

        if expires_at and expires_at <= utc_now_naive():
            await delete_ai_document(payload.file_id, user_id)
            raise HTTPException(
                status_code=410,
                detail="Uploaded document expired. Please upload again.",
            )

        if not file_path or not Path(file_path).exists():
            await delete_ai_document(payload.file_id, user_id)
            raise HTTPException(
                status_code=404,
                detail="Uploaded document file not found.",
            )

        await ai_documents_collection.update_one(
            {"_id": payload.file_id, "user_id": user_id},
            {
                "$set": {
                    "status": "processing",
                    "processing_started_at": utc_now_naive(),
                }
            },
        )

        result = await extractor(
            document_url=f"local_file:{file_path}",
            subsection=payload.subsection,
            mime_type=mime_type,
        )

        return {
            "success": True,
            "section": payload.section,
            "scope": "subsection" if payload.subsection else "section",
            "subsection": payload.subsection,
            "result": result,
        }

    except HTTPException:
        raise

    except Exception as e:
        print("❌ AI autofill failed:", repr(e))
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail="AI autofill failed. Please try again.",
        )

    finally:
        await delete_ai_document(payload.file_id, user_id)