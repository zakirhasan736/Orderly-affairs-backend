# app/ai/ai_autofill_routes.py

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.ai.autofill_registry import SECTION_EXTRACTORS
from app.ai.cross_section_enrichment import (
    build_detected_facts_payload,
    cached_extraction_needs_full_read,
    enrich_primary_result,
    insurance_result_is_thin,
    is_cross_seed_extraction,
    mark_full_extraction,
    merge_seed_into_cached,
    seed_insurance_from_vehicles,
    seed_vehicles_from_insurance,
    sync_vehicle_insurance_shared_fields,
)
from app.ai.document_classifier import (
    build_additional_sections_payload,
    classify_document_for_section,
    enforce_upload_section_first,
    get_section_meta,
    harden_vehicle_insurance_routing,
)
from app.ai.field_catalog import (
    build_fast_section_previews_from_classification,
    build_section_previews_payload,
)
from app.ai.llm_generate import (
    LLMServiceUnavailableError,
    is_quota_exhausted_error,
)
from app.ai.ai_auth import get_current_owner, get_user_id
from app.ai.background_section_persist import persist_cached_extractions_for_owner
from app.ai.llm_context import clear_llm_settings, set_llm_settings
from app.ai.llm_generate import active_brain_info
from app.ai.local_document_extract import describe_read_source
from app.ai.skill_memory import (
    fetch_few_shot_examples,
    format_few_shot_prompt,
    learning_enabled,
    record_successful_fill,
)
from app.database import ai_documents_collection
from app.storage.vault import resolve_stored_ai_document_path

# Back-compat name used in except blocks below
GeminiServiceUnavailableError = LLMServiceUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai-autofill"])

# Prefetch insurance when filling vehicles. Do NOT auto-prefetch vehicles for
# every insurance doc (life/home/health) — only classification/auto signals add vehicles.
FAST_PARTNER_PREFETCH: dict[str, list[str]] = {
    "vehicles": ["insurance_policies"],
}


def _document_text_hint(file_path: str | None, mime_type: str | None, doc: dict | None = None) -> str:
    """OCR/text snapshot for routing hardeners (prefer upload-time extract)."""
    if isinstance(doc, dict):
        cached = str(doc.get("extracted_text") or "").strip()
        if cached:
            return cached[:20000]
    if not file_path:
        return ""
    try:
        from app.ai.local_document_extract import extract_document_text

        meta = extract_document_text(file_path, mime_type)
        return str(meta.get("text") or "")[:20000]
    except Exception:
        return ""


class AutofillSectionRequest(BaseModel):
    section: str
    file_id: str = Field(..., min_length=16, max_length=100)
    subsection: str | None = None
    field_catalog: list[dict] | None = None
    use_routed_cache: bool = False
    # Dashboard overview: classify only — do not extract into the probe section.
    classify_only: bool = False


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
    cached = _get_cached_extraction(doc, section_key)
    if not cached:
        return False

    # Cross-seeds / thin partner bridges must never short-circuit a full
    # section field-catalog extract against the uploaded document.
    if cached_extraction_needs_full_read(section_key, cached):
        return False

    # Document was already fully read for this section — reuse it.
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
        # Allow re-extract when prior fill was only a thin cross-seed.
        cached = _get_cached_extraction(doc, section_key)
        if not cached_extraction_needs_full_read(section_key, cached):
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

    # Hard-paired partners (vehicles ↔ insurance) when this upload already
    # touched one side of the pair — still extract without re-classifying.
    related = set(pending) | set(consumed) | additional_keys
    routed = doc.get("routed_section")
    if routed:
        related.add(routed)
    for primary, partners in FAST_PARTNER_PREFETCH.items():
        pair = {primary, *partners}
        if section_key in pair and related.intersection(pair):
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


MAX_BACKGROUND_PREFETCH_SECTIONS = 8


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

    # Prefer speed: extract the matched section + close partners in parallel.
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
            field_catalog=None,  # per-section rich catalogs built inside cache
            sequential=False,
        )

        fresh = await ai_documents_collection.find_one(
            {"_id": file_id, "user_id": user_id},
            {"cached_extractions": 1},
        )
        # Cache only — do NOT vault-persist here. Prefetch + finalize + partner
        # fill were each writing vehicles and creating duplicate rows.
        _ = (fresh or doc).get("cached_extractions") or {}
    except Exception as error:
        logger.warning("Background section prefetch failed: %s", repr(error))


def _limit_prefetch_keys(best_key: str, extra_keys: list[str]) -> list[str]:
    partners = FAST_PARTNER_PREFETCH.get(best_key, [])
    ordered: list[str] = []
    for key in [best_key, *partners, *extra_keys]:
        if key and key in SECTION_EXTRACTORS and key not in ordered:
            ordered.append(key)
    return ordered[:MAX_BACKGROUND_PREFETCH_SECTIONS]


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

    catalog = field_catalog
    if not catalog:
        try:
            from app.ai.section_field_ssot import build_rich_catalog_for_section

            catalog = build_rich_catalog_for_section(section_key) or None
        except Exception:
            catalog = None

    return await extractor(
        document_url=f"local_file:{file_path}",
        subsection=resolved_subsection,
        mime_type=mime_type,
        field_catalog=catalog,
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
    sequential: bool = False,
):
    if not additional_sections:
        return

    fresh = await ai_documents_collection.find_one(
        {"_id": file_id, "user_id": user_id},
        {"cached_extractions": 1},
    )
    cached_extractions = dict((fresh or doc).get("cached_extractions") or {})

    async def cache_one(section_key: str):
        if section_key == exclude_section:
            return

        existing = cached_extractions.get(section_key)
        # Re-extract when prior cache is only a partner seed / too thin.
        if existing and not cached_extraction_needs_full_read(section_key, existing):
            return

        try:
            # Always use the target section's own rich catalog — never the
            # caller's catalog (which belongs to a different section).
            result = await _run_extractor(
                section_key,
                file_path=file_path,
                mime_type=mime_type,
                subsection=None,
                field_catalog=None,
            )
            if isinstance(result, dict):
                full = mark_full_extraction(result) or result
                # Keep useful shared fields from a prior seed.
                if existing and is_cross_seed_extraction(existing):
                    array_key = "7A" if section_key == "insurance_policies" else (
                        "5A" if section_key == "vehicles" else None
                    )
                    if array_key:
                        full = (
                            merge_seed_into_cached(full, existing, array_key=array_key)
                            or full
                        )
                        full = mark_full_extraction(full) or full
                cached_extractions[section_key] = full
        except Exception as error:
            logger.warning(
                "Failed to pre-cache section %s: %s",
                section_key,
                repr(error),
            )

    if sequential:
        for item in additional_sections:
            await cache_one(item["section_key"])
    else:
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

    extract_meta = {}
    if isinstance(result, dict):
        raw_meta = result.pop("__extract_meta", None)
        if isinstance(raw_meta, dict):
            extract_meta = raw_meta

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

    # Canonicalize wording → exact section field keys before cache/cross-seed.
    result = enrich_primary_result(result, payload.section) or result
    if not from_cache:
        result = mark_full_extraction(result) or result

    cached_extractions = dict(source_doc.get("cached_extractions") or {})
    previous_cached = cached_extractions.get(payload.section)
    cached_extractions[payload.section] = result

    # Keep useful fields from a prior thin seed when a fresh extract wins.
    if (
        previous_cached
        and payload.section == "insurance_policies"
        and not from_cache
    ):
        cached_extractions["insurance_policies"] = merge_seed_into_cached(
            result,
            previous_cached,
            array_key="7A",
        ) or result
        result = cached_extractions["insurance_policies"]
    elif (
        previous_cached
        and payload.section == "vehicles"
        and not from_cache
    ):
        cached_extractions["vehicles"] = merge_seed_into_cached(
            result,
            previous_cached,
            array_key="5A",
        ) or result
        result = cached_extractions["vehicles"]

    # Cross-place shared vehicle <-> insurance fields so policy numbers aren't missed.
    if payload.section == "vehicles":
        insurance_seed = seed_insurance_from_vehicles(result)
        if insurance_seed:
            cached_extractions["insurance_policies"] = merge_seed_into_cached(
                cached_extractions.get("insurance_policies"),
                insurance_seed,
                array_key="7A",
            )
            partner_in_additional = any(
                item.get("section_key") == "insurance_policies"
                for item in additional_sections
            )
            if not partner_in_additional:
                additional_sections.append(
                    {
                        "section_key": "insurance_policies",
                        "section_id": (get_section_meta("insurance_policies") or {}).get(
                            "id"
                        ),
                        "section_label": (get_section_meta("insurance_policies") or {}).get(
                            "label"
                        ),
                        "confidence": "medium",
                        "data_summary": "Insurance policy details found on this vehicle document.",
                    }
                )
    elif payload.section == "insurance_policies":
        vehicle_seed = seed_vehicles_from_insurance(result)
        if vehicle_seed:
            cached_extractions["vehicles"] = merge_seed_into_cached(
                cached_extractions.get("vehicles"),
                vehicle_seed,
                array_key="5A",
            )
            partner_in_additional = any(
                item.get("section_key") == "vehicles"
                for item in additional_sections
            )
            if not partner_in_additional:
                additional_sections.append(
                    {
                        "section_key": "vehicles",
                        "section_id": (get_section_meta("vehicles") or {}).get("id"),
                        "section_label": (get_section_meta("vehicles") or {}).get("label"),
                        "confidence": "medium",
                        "data_summary": "Vehicle details found on this insurance document.",
                    }
                )

    # Bidirectional meaning sync: policy/insurance number, company, expiry
    # must appear on BOTH vehicles and insurance when present on either side.
    cached_extractions = sync_vehicle_insurance_shared_fields(cached_extractions)

    # If insurance is still thin after sync but vehicles have policy data, force seed.
    if insurance_result_is_thin(cached_extractions.get("insurance_policies")):
        forced = seed_insurance_from_vehicles(cached_extractions.get("vehicles"))
        if forced:
            cached_extractions["insurance_policies"] = merge_seed_into_cached(
                cached_extractions.get("insurance_policies"),
                forced,
                array_key="7A",
            )
            cached_extractions = sync_vehicle_insurance_shared_fields(
                cached_extractions
            )

    if payload.section in cached_extractions:
        result = cached_extractions.get(payload.section) or result

    # Insurance primary must never return blank when vehicles already hold the policy.
    if payload.section == "insurance_policies" and insurance_result_is_thin(result):
        synced_insurance = cached_extractions.get("insurance_policies")
        if synced_insurance and not insurance_result_is_thin(synced_insurance):
            result = synced_insurance
            cached_extractions["insurance_policies"] = result

    detected_facts = build_detected_facts_payload(
        primary_section=payload.section,
        primary_result=result,
        cached_extractions=cached_extractions,
    )

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
        # Keep the file after the first successful fill so the owner can
        # re-open the section / press Auto-fill again without re-uploading.
        # TTL cleanup still removes expired docs.
        keep_document = True

    if keep_document:
        await ai_documents_collection.update_one(
            {"_id": file_id, "user_id": user_id},
            {"$set": update_fields},
        )
    else:
        await delete_ai_document(file_id, user_id)

    # Day-to-day skill growth: remember successful OCR→JSON fills for own model.
    if not from_cache:
        patch = result.get("patch") if isinstance(result, dict) else None
        doc_text = str(
            extract_meta.get("document_text")
            or source_doc.get("extracted_text")
            or ""
        )
        try:
            from app.ai.llm_context import get_llm_settings as _gls
            from app.ai.skill_memory import learning_enabled as _learn_on

            brain = _gls()
            should_learn = _learn_on(brain)
            few_shot_prompt = str(brain.get("few_shot_prompt") or "")
            few_shot_count = few_shot_prompt.count("--- Example ")
        except Exception:
            brain = {}
            should_learn = True
            few_shot_count = 0
        if should_learn:
            asyncio.create_task(
                record_successful_fill(
                    user_id=user_id,
                    section_key=payload.section,
                    document_text=doc_text,
                    patch=patch if isinstance(patch, dict) else None,
                    confidence=(result or {}).get("confidence")
                    if isinstance(result, dict)
                    else None,
                    provider=extract_meta.get("teacher_provider")
                    or brain.get("provider"),
                    model=extract_meta.get("teacher_model") or brain.get("model"),
                    file_id=file_id,
                    mime_type=source_doc.get("mime_type"),
                    extract_meta=extract_meta,
                    classification=classification
                    if isinstance(classification, dict)
                    else None,
                    field_catalog=extract_meta.get("field_catalog")
                    or payload.field_catalog,
                    system_prompt=extract_meta.get("system_prompt"),
                    user_prompt=extract_meta.get("user_prompt"),
                    few_shot_count=few_shot_count,
                    usage=extract_meta.get("usage"),
                    result=result if isinstance(result, dict) else None,
                )
            )

    section_previews = build_section_previews_payload(
        filled_section_key=payload.section,
        filled_result=result,
        additional_sections=additional_sections,
        cached_extractions=cached_extractions,
        field_catalog=payload.field_catalog,
    )

    document_summary = classification.get("document_summary")

    if not extract_meta and isinstance(doc, dict):
        extract_meta = {
            "method": doc.get("extract_method"),
            "quality_score": doc.get("extract_quality"),
            "needs_vision": False,
            "llm_input": "text",
            "gemini_input": "text",
            "read_source": (
                "cache"
                if from_cache or doc.get("extract_reuse")
                else "system"
            ),
        }

    read_source = describe_read_source(
        extract_meta,
        from_cache=from_cache or bool(doc.get("extract_reuse")),
    )

    if extract_meta:
        await ai_documents_collection.update_one(
            {"_id": file_id, "user_id": user_id},
            {
                "$set": {
                    "last_extract_meta": extract_meta,
                    "read_source": read_source,
                }
            },
        )

    # Only return FULL partner extracts here. Cross-seeds are bridges — the
    # client must run a catalog extract per related section against the file.
    partner_results: dict[str, dict] = {}
    for partner_key, partner_result in cached_extractions.items():
        if partner_key == payload.section:
            continue
        if not isinstance(partner_result, dict):
            continue
        if cached_extraction_needs_full_read(partner_key, partner_result):
            continue
        if partner_key in FAST_PARTNER_PREFETCH.get(payload.section, []) or any(
            item.get("section_key") == partner_key for item in additional_sections
        ):
            partner_results[partner_key] = partner_result

    # Persist PRIMARY only. Partner sections are filled+persisted sequentially
    # by the overview runner — writing partners here duplicated vehicle rows.
    persist_keys = [payload.section]
    asyncio.create_task(
        persist_cached_extractions_for_owner(
            owner_id=user_id,
            cached_extractions=cached_extractions,
            section_keys=persist_keys,
        )
    )

    return {
        "success": True,
        "section": payload.section,
        "scope": "subsection" if payload.subsection else "section",
        "subsection": payload.subsection,
        "result": result,
        "additional_sections": additional_sections,
        "section_previews": section_previews,
        "detected_facts": detected_facts,
        "partner_results": partner_results,
        "document_summary": document_summary,
        "file_kept": keep_document,
        "from_cache": from_cache,
        "document_deleted": not keep_document,
        "read_source": read_source,
        "extract_method": extract_meta.get("method"),
        "extract_meta": extract_meta,
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
            detail={
                "code": "ai_document_missing",
                "message": "Document not found, expired, or already processed.",
            },
        )

    resolved = resolve_stored_ai_document_path(doc)
    file_path = str(resolved) if resolved else None
    mime_type = doc.get("mime_type")
    keep_document = False

    brain = {**active_brain_info(), "learning_enabled": learning_enabled()}
    doc_text = str(doc.get("extracted_text") or "")
    few_shot = ""
    if brain.get("learning_enabled", True) and doc_text.strip():
        examples = await fetch_few_shot_examples(
            user_id=user_id,
            section_key=payload.section,
            document_text=doc_text,
        )
        few_shot = format_few_shot_prompt(examples)
    set_llm_settings({**brain, "few_shot_prompt": few_shot})

    try:
        expires_at = normalize_mongo_datetime(doc.get("expires_at"))

        if expires_at and expires_at <= utc_now_naive():
            await delete_ai_document(payload.file_id, user_id)
            raise HTTPException(
                status_code=410,
                detail={
                    "code": "ai_document_expired",
                    "message": "Uploaded document expired. Please upload again.",
                },
            )

        if not resolved:
            # Keep the DB row — path may be fixable after vault/config deploy.
            logger.warning(
                "AI document file missing on disk file_id=%s user_id=%s path=%s folder_uuid=%s",
                payload.file_id,
                user_id,
                doc.get("path"),
                doc.get("folder_uuid"),
            )
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "ai_document_file_missing",
                    "message": "Uploaded document file not found. Please upload again.",
                },
            )

        # Heal stale absolute paths after VAULT_ROOT / cwd changes.
        if str(doc.get("path") or "") != file_path:
            await ai_documents_collection.update_one(
                {"_id": payload.file_id, "user_id": user_id},
                {"$set": {"path": file_path, "updated_at": utc_now_naive()}},
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

        # Exact-byte re-upload: reuse prior classification — skip Gemini classify.
        reused_classification = doc.get("last_classification")
        if (
            payload.classify_only
            and doc.get("extract_reuse")
            and isinstance(reused_classification, dict)
            and reused_classification
        ):
            classification = harden_vehicle_insurance_routing(
                dict(reused_classification),
                document_text=_document_text_hint(file_path, mime_type, doc),
            )
            matches_requested = bool(classification.get("matches_requested_section"))
            best_section_key = classification.get("best_section_key") or payload.section
            suggested = get_section_meta(best_section_key) or {}
            additional_sections = build_additional_sections_payload(
                classification,
                best_section_key,
            )
            section_previews = build_fast_section_previews_from_classification(
                classification,
                suggested_section_key=best_section_key,
            )
            prefetch_keys = _limit_prefetch_keys(
                best_section_key,
                [
                    item["section_key"]
                    for item in additional_sections
                    if item.get("section_key")
                ],
            )
            await ai_documents_collection.update_one(
                {"_id": payload.file_id, "user_id": user_id},
                {
                    "$set": {
                        "status": "uploaded",
                        "last_classification": classification,
                        "routed_section": best_section_key,
                        "pending_sections": [
                            key
                            for key in prefetch_keys
                            if key in SECTION_EXTRACTORS
                        ],
                    }
                },
            )
            # Prefetch only missing sections; cached partners are skipped inside.
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
            return {
                "success": True,
                "classified_only": True,
                "from_cache": True,
                "extract_reuse": True,
                "unchanged": True,
                "section": payload.section,
                "best_section": best_section_key,
                "best_section_id": suggested.get("id"),
                "best_section_label": suggested.get("label"),
                "best_subsection": suggested.get("default_subsection"),
                "matches_requested_section": matches_requested,
                "document_summary": classification.get("document_summary"),
                "additional_sections": additional_sections,
                "section_previews": section_previews,
                "file_id": payload.file_id,
                "mime_type": mime_type,
                "file_kept": True,
            }

        classification = await classify_document_for_section(
            document_url=f"local_file:{file_path}",
            mime_type=mime_type,
            requested_section_key=payload.section,
        )
        # Overview classify_only must not force the probe section.
        doc_text_hint = _document_text_hint(file_path, mime_type, doc)
        if not payload.classify_only:
            classification = enforce_upload_section_first(
                classification,
                payload.section,
                document_text=doc_text_hint,
            )
        else:
            # Still apply vehicle/insurance pair helper when probing those sections.
            if payload.section in {"vehicles", "insurance_policies"}:
                classification = enforce_upload_section_first(
                    classification,
                    payload.section,
                    document_text=doc_text_hint,
                )

        matches_requested = bool(classification.get("matches_requested_section"))
        best_section_key = classification.get("best_section_key") or payload.section

        if payload.classify_only:
            # Always harden auto/vehicle routing even for overview probes.
            classification = harden_vehicle_insurance_routing(
                classification,
                document_text=doc_text_hint,
            )
            # Re-read after harden — best_section may have changed.
            matches_requested = bool(classification.get("matches_requested_section"))
            best_section_key = classification.get("best_section_key") or payload.section
            suggested = get_section_meta(best_section_key) or {}
            additional_sections = build_additional_sections_payload(
                classification,
                best_section_key,
            )
            section_previews = build_fast_section_previews_from_classification(
                classification,
                suggested_section_key=best_section_key,
            )

            prefetch_keys = _limit_prefetch_keys(
                best_section_key,
                [
                    item["section_key"]
                    for item in additional_sections
                    if item.get("section_key")
                ],
            )

            await ai_documents_collection.update_one(
                {"_id": payload.file_id, "user_id": user_id},
                {
                    "$set": {
                        "status": "uploaded",
                        "last_classification": classification,
                        "routed_section": best_section_key,
                        "pending_sections": [
                            key
                            for key in prefetch_keys
                            if key in SECTION_EXTRACTORS
                        ],
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
            return {
                "success": True,
                "classified_only": True,
                "section": payload.section,
                "best_section": best_section_key,
                "best_section_id": suggested.get("id"),
                "best_section_label": suggested.get("label"),
                "best_subsection": suggested.get("default_subsection"),
                "matches_requested_section": matches_requested,
                "document_summary": classification.get("document_summary"),
                "additional_sections": additional_sections,
                "section_previews": section_previews,
                "file_id": payload.file_id,
                "mime_type": mime_type,
                "file_kept": True,
            }

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
                    field_catalog=payload.field_catalog,
                    sequential=False,
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

    except ValueError as error:
        await ai_documents_collection.update_one(
            {"_id": payload.file_id, "user_id": user_id},
            {"$set": {"status": "uploaded"}},
        )
        keep_document = True
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_autofill_request",
                "message": str(error),
            },
        ) from error

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
        if is_quota_exhausted_error(error):
            await ai_documents_collection.update_one(
                {"_id": payload.file_id, "user_id": user_id},
                {"$set": {"status": "uploaded"}},
            )
            keep_document = True
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
    finally:
        clear_llm_settings()
