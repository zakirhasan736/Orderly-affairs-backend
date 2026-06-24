# app/ai/ai_autofill_routes.py

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from google.genai.errors import ClientError
from pydantic import BaseModel, Field

from app.ai.autofill_registry import SECTION_EXTRACTORS
from app.ai.document_classifier import (
    build_additional_sections_payload,
    classify_document_for_section,
    enforce_upload_section_first,
    get_section_meta,
)
from app.ai.field_catalog import (
    build_fast_section_previews_from_classification,
    build_section_previews_payload,
)
from app.ai.gemini_generate import (
    GeminiServiceUnavailableError,
    is_quota_exhausted_error,
)
from app.ai.ai_auth import get_current_owner, get_user_id
from app.database import ai_documents_collection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai-autofill"])


class AutofillSectionRequest(BaseModel):
    section: str
    file_id: str = Field(..., min_length=16, max_length=100)
    subsection: str | None = None
    field_catalog: list[dict] | None = None
    use_routed_cache: bool = False


def utc_now_naive():
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


async def delete_ai_document(file_id: str, user_id: str):
    doc = await ai_documents_collection.find_one(
        {"_id": file_id, "user_id": user_id},
        {"path": 1},
    )

    if doc:
        safe_delete_file(doc.get("path"))

    await ai_documents_collection.delete_one({"_id": file_id, "user_id": user_id})


def _get_cached_extraction(doc: dict, section_key: str) -> dict | None:
    cached = doc.get("cached_extractions") or {}
    result = cached.get(section_key)
    return result if isinstance(result, dict) else None


def _should_use_cached_extraction(
    doc: dict,
    section_key: str,
    *,
    use_routed_cache: bool,
) -> bool:
    if not _get_cached_extraction(doc, section_key):
        return False

    # Document was already read for this section — never call Gemini again.
    return True


def _should_extract_without_classification(
    doc: dict,
    section_key: str,
    *,
    use_routed_cache: bool,
) -> bool:
    """Multi-section routed fills must extract directly — never re-classify."""
    if not use_routed_cache:
        return False

    consumed = set(doc.get("consumed_sections") or [])
    if section_key in consumed:
        return False

    pending = doc.get("pending_sections") or []
    if section_key in pending:
        return True

    if _get_cached_extraction(doc, section_key):
        return True

    if doc.get("routed_section") == section_key:
        return True

    classification = doc.get("last_classification") or {}
    additional_keys = {
        item.get("section_key")
        for item in (classification.get("additional_sections") or [])
        if isinstance(item, dict) and item.get("section_key")
    }
    if section_key in additional_keys:
        return True

    return False


async def _extract_section_without_classification(
    *,
    file_id: str,
    user_id: str,
    doc: dict,
    payload: AutofillSectionRequest,
    file_path: str,
    mime_type: str,
    extractor,
):
    classification = doc.get("last_classification") or {}

    await ai_documents_collection.update_one(
        {"_id": file_id, "user_id": user_id},
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
        field_catalog=payload.field_catalog,
    )

    fresh_doc = await ai_documents_collection.find_one(
        {"_id": file_id, "user_id": user_id},
    )
    return await _finalize_autofill_success(
        file_id=file_id,
        user_id=user_id,
        doc=fresh_doc or doc,
        payload=payload,
        result=result,
        classification=classification,
        from_cache=False,
    )


async def _safe_cache_additional_sections(**kwargs):
    try:
        await _cache_additional_sections(**kwargs)
    except Exception as error:
        logger.warning("Background section pre-cache failed: %s", repr(error))


MAX_BACKGROUND_PREFETCH_SECTIONS = 2


async def _safe_prefetch_sections(
    *,
    file_id: str,
    user_id: str,
    doc: dict,
    file_path: str,
    mime_type: str,
    section_keys: list[str],
):
    if not section_keys:
        return

    limited_keys = section_keys[:MAX_BACKGROUND_PREFETCH_SECTIONS]
    additional_sections = [{"section_key": key} for key in limited_keys]

    try:
        await _cache_additional_sections(
            file_id=file_id,
            user_id=user_id,
            doc=doc,
            file_path=file_path,
            mime_type=mime_type,
            additional_sections=additional_sections,
            exclude_section="__none__",
            field_catalog=None,
        )
    except Exception as error:
        logger.warning("Background section prefetch failed: %s", repr(error))


async def _run_extractor(
    section_key: str,
    *,
    file_path: str,
    mime_type: str,
    subsection: str | None,
    field_catalog: list[dict] | None,
):
    extractor = SECTION_EXTRACTORS.get(section_key)
    if not extractor:
        return None

    meta = get_section_meta(section_key) or {}
    resolved_subsection = subsection or meta.get("default_subsection")

    return await extractor(
        document_url=f"local_file:{file_path}",
        subsection=resolved_subsection,
        mime_type=mime_type,
        field_catalog=field_catalog,
    )


async def _cache_additional_sections(
    *,
    file_id: str,
    user_id: str,
    doc: dict,
    file_path: str,
    mime_type: str,
    additional_sections: list[dict],
    exclude_section: str,
    field_catalog: list[dict] | None = None,
):
    if not additional_sections:
        return

    fresh = await ai_documents_collection.find_one(
        {"_id": file_id, "user_id": user_id},
        {"cached_extractions": 1},
    )
    cached_extractions = dict((fresh or doc).get("cached_extractions") or {})

    async def cache_one(section_key: str):
        if section_key in cached_extractions or section_key == exclude_section:
            return

        try:
            result = await _run_extractor(
                section_key,
                file_path=file_path,
                mime_type=mime_type,
                subsection=None,
                field_catalog=field_catalog,
            )
            if isinstance(result, dict):
                cached_extractions[section_key] = result
        except Exception as error:
            logger.warning(
                "Failed to pre-cache section %s: %s",
                section_key,
                repr(error),
            )

    await asyncio.gather(
        *[cache_one(item["section_key"]) for item in additional_sections]
    )

    await ai_documents_collection.update_one(
        {"_id": file_id, "user_id": user_id},
        {"$set": {"cached_extractions": cached_extractions}},
    )


async def _finalize_autofill_success(
    *,
    file_id: str,
    user_id: str,
    doc: dict,
    payload: AutofillSectionRequest,
    result: dict,
    classification: dict,
    from_cache: bool = False,
) -> dict:
    fresh_doc = await ai_documents_collection.find_one(
        {"_id": file_id, "user_id": user_id},
    )
    source_doc = fresh_doc or doc

    existing_pending = list(source_doc.get("pending_sections") or [])
    existing_consumed = list(source_doc.get("consumed_sections") or [])

    if payload.section not in existing_consumed:
        existing_consumed.append(payload.section)

    additional_sections = build_additional_sections_payload(
        classification,
        payload.section,
    )
    consumed_set = set(existing_consumed)
    additional_sections = [
        item
        for item in additional_sections
        if item["section_key"] not in consumed_set
    ]

    cached_extractions = dict(source_doc.get("cached_extractions") or {})
    cached_extractions[payload.section] = result

    if doc.get("routed_section") == payload.section:
        routed_section = None
    else:
        routed_section = source_doc.get("routed_section")

    keep_document = False
    update_fields: dict = {
        "status": "uploaded",
        "consumed_sections": existing_consumed,
        "cached_extractions": cached_extractions,
        "last_classification": classification,
    }

    if routed_section is not None:
        update_fields["routed_section"] = routed_section

    if additional_sections:
        keep_document = True
        pending_keys = existing_pending[:]
        for item in additional_sections:
            section_key = item["section_key"]
            if (
                section_key not in pending_keys
                and section_key not in consumed_set
            ):
                pending_keys.append(section_key)
        update_fields["pending_sections"] = pending_keys
    elif existing_pending:
        remaining_pending = [
            key for key in existing_pending if key != payload.section
        ]
        if remaining_pending:
            keep_document = True
            update_fields["pending_sections"] = remaining_pending
        else:
            keep_document = False
    else:
        keep_document = False

    if keep_document:
        await ai_documents_collection.update_one(
            {"_id": file_id, "user_id": user_id},
            {"$set": update_fields},
        )
    else:
        await delete_ai_document(file_id, user_id)

    section_previews = build_section_previews_payload(
        filled_section_key=payload.section,
        filled_result=result,
        additional_sections=additional_sections,
        cached_extractions=cached_extractions,
        field_catalog=payload.field_catalog,
    )

    document_summary = classification.get("document_summary")

    return {
        "success": True,
        "section": payload.section,
        "scope": "subsection" if payload.subsection else "section",
        "subsection": payload.subsection,
        "result": result,
        "additional_sections": additional_sections,
        "section_previews": section_previews,
        "document_summary": document_summary,
        "file_kept": keep_document,
        "from_cache": from_cache,
        "document_deleted": not keep_document,
    }


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
    keep_document = False

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

        latest_doc = await ai_documents_collection.find_one(
            {"_id": payload.file_id, "user_id": user_id, "status": "uploaded"},
        )
        if latest_doc:
            doc = latest_doc

        if _should_use_cached_extraction(
            doc,
            payload.section,
            use_routed_cache=payload.use_routed_cache,
        ):
            cached_result = _get_cached_extraction(doc, payload.section)
            if cached_result:
                classification = doc.get("last_classification") or {}
                keep_document = True
                response = await _finalize_autofill_success(
                    file_id=payload.file_id,
                    user_id=user_id,
                    doc=doc,
                    payload=payload,
                    result=cached_result,
                    classification=classification,
                    from_cache=True,
                )
                return response

        if _should_extract_without_classification(
            doc,
            payload.section,
            use_routed_cache=payload.use_routed_cache,
        ):
            return await _extract_section_without_classification(
                file_id=payload.file_id,
                user_id=user_id,
                doc=doc,
                payload=payload,
                file_path=file_path,
                mime_type=mime_type,
                extractor=extractor,
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

        classification = await classify_document_for_section(
            document_url=f"local_file:{file_path}",
            mime_type=mime_type,
            requested_section_key=payload.section,
        )
        classification = enforce_upload_section_first(classification, payload.section)

        matches_requested = bool(classification.get("matches_requested_section"))
        best_section_key = classification.get("best_section_key") or payload.section

        # Block only when the document has no data for the requested section.
        sections_conflict = (
            not matches_requested
            and best_section_key != payload.section
            and best_section_key in SECTION_EXTRACTORS
            and not payload.use_routed_cache
        )

        if sections_conflict:
            suggested = get_section_meta(best_section_key) or {}

            additional_sections = [
                item
                for item in build_additional_sections_payload(
                    classification,
                    payload.section,
                )
                if item["section_key"] != best_section_key
            ]

            section_previews = build_fast_section_previews_from_classification(
                classification,
                suggested_section_key=best_section_key,
            )

            prefetch_keys = [best_section_key] + [
                item["section_key"]
                for item in additional_sections
                if item.get("section_key")
            ]

            await ai_documents_collection.update_one(
                {"_id": payload.file_id, "user_id": user_id},
                {
                    "$set": {
                        "status": "uploaded",
                        "last_classification": classification,
                        "routed_section": best_section_key,
                    }
                },
            )

            asyncio.create_task(
                _safe_prefetch_sections(
                    file_id=payload.file_id,
                    user_id=user_id,
                    doc=doc,
                    file_path=file_path,
                    mime_type=mime_type,
                    section_keys=prefetch_keys,
                )
            )

            keep_document = True

            raise HTTPException(
                status_code=409,
                detail={
                    "code": "section_mismatch",
                    "mismatch_type": "wrong_section",
                    "message": "This document does not appear to belong to the current section.",
                    "requested_section": payload.section,
                    "suggested_section": best_section_key,
                    "suggested_section_id": suggested.get("id"),
                    "suggested_section_label": suggested.get("label"),
                    "suggested_subsection": suggested.get("default_subsection"),
                    "document_summary": classification.get("document_summary"),
                    "extracted_fields": [],
                    "additional_sections": additional_sections,
                    "section_previews": section_previews,
                    "file_id": payload.file_id,
                    "mime_type": mime_type,
                },
            )

        result = await extractor(
            document_url=f"local_file:{file_path}",
            subsection=payload.subsection,
            mime_type=mime_type,
            field_catalog=payload.field_catalog,
        )

        additional_sections = build_additional_sections_payload(
            classification,
            payload.section,
        )

        await ai_documents_collection.update_one(
            {"_id": payload.file_id, "user_id": user_id},
            {"$set": {"last_classification": classification}},
        )

        if additional_sections:
            asyncio.create_task(
                _safe_cache_additional_sections(
                    file_id=payload.file_id,
                    user_id=user_id,
                    doc=doc,
                    file_path=file_path,
                    mime_type=mime_type,
                    additional_sections=additional_sections[
                        :MAX_BACKGROUND_PREFETCH_SECTIONS
                    ],
                    exclude_section=payload.section,
                    field_catalog=None,
                )
            )

        keep_document = True
        fresh_doc = await ai_documents_collection.find_one(
            {"_id": payload.file_id, "user_id": user_id},
        )
        return await _finalize_autofill_success(
            file_id=payload.file_id,
            user_id=user_id,
            doc=fresh_doc or doc,
            payload=payload,
            result=result,
            classification=classification,
            from_cache=False,
        )

    except HTTPException:
        raise

    except GeminiServiceUnavailableError as error:
        await ai_documents_collection.update_one(
            {"_id": payload.file_id, "user_id": user_id},
            {"$set": {"status": "uploaded"}},
        )
        keep_document = True
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ai_service_unavailable",
                "message": str(error),
            },
        ) from error

    except Exception as error:
        if isinstance(error, ClientError) and is_quota_exhausted_error(error):
            await ai_documents_collection.update_one(
                {"_id": payload.file_id, "user_id": user_id},
                {"$set": {"status": "uploaded"}},
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "ai_service_unavailable",
                    "message": "AI quota is busy. Please wait a minute and try Auto-fill again.",
                },
            ) from error

        print("❌ AI autofill failed:", repr(error))
        traceback.print_exc()
        await ai_documents_collection.update_one(
            {"_id": payload.file_id, "user_id": user_id},
            {"$set": {"status": "uploaded"}},
        )
        keep_document = True
        raise HTTPException(
            status_code=500,
            detail="AI autofill failed. Please try again.",
        )
