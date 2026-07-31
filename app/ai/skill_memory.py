# app/ai/skill_memory.py
"""
Day-to-day Orderly skill corpus (MongoDB `ai_skill_examples`).

NOT shown to vault owners in the UI. Runs silently on each successful fill
when AI_LEARNING_ENABLED=true. Admin export / own-model training comes later.

Collections: ai_skill_examples, ai_brain_settings (future admin toggles).
See app/ai/SKILL_MEMORY.md for admin mount checklist.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from app.database import ai_skill_examples_collection

logger = logging.getLogger(__name__)

SKILL_SCHEMA_VERSION = "orderly_skill_v2"
MAX_TEXT_STORE = 50000
MAX_PROMPT_STORE = 40000
MAX_FEW_SHOT = 2
MAX_FEW_SHOT_CHARS = 3500

ORDERLY_IDEOLOGY = {
    "pipeline": "local_ocr_or_pdf_text → llm_json_section_fill",
    "goal": (
        "Map document wording into exact Orderly Affairs vault section fields "
        "so next-of-kin can find critical information."
    ),
    "decision_policy": [
        "Use ONLY facts present in the document text.",
        "Map synonyms to the exact catalog field keys (never invent new keys).",
        "Prefer dedicated fields over dumping into notes.",
        "Omit empty/null fields when possible.",
        "Never extract raw passwords, full SSN, or full payment card numbers.",
        "If multiple entities exist, keep one object per entity.",
        "When unsure, lower confidence rather than hallucinating.",
    ],
    "privacy_rules": [
        "No passwords",
        "No full SSN",
        "No full card numbers (last4 only if schema asks)",
    ],
}


def learning_enabled(settings: dict[str, Any] | None = None) -> bool:
    if settings is None:
        try:
            from app.ai.llm_context import get_llm_settings

            settings = get_llm_settings()
        except Exception:
            settings = None
    if isinstance(settings, dict) and "learning_enabled" in settings:
        return bool(settings.get("learning_enabled"))
    raw = (os.getenv("AI_LEARNING_ENABLED") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _token_set(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower())}


def _overlap_score(a: str, b: str) -> float:
    sa, sb = _token_set(a), _token_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(len(sa | sb))


def build_skill_record(
    *,
    user_id: str,
    section_key: str,
    document_text: str,
    patch: dict[str, Any] | None,
    confidence: Any = None,
    provider: str | None = None,
    model: str | None = None,
    file_id: str | None = None,
    mime_type: str | None = None,
    extract_meta: dict[str, Any] | None = None,
    classification: dict[str, Any] | None = None,
    field_catalog: list[dict] | None = None,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    few_shot_count: int = 0,
    usage: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    text = (document_text or "").strip()
    if len(text) < 40:
        return None
    if not isinstance(patch, dict) or not patch:
        return None

    catalog_keys: list[str] = []
    for item in field_catalog or []:
        if isinstance(item, dict) and item.get("key"):
            catalog_keys.append(str(item["key"]))

    assistant_json = json.dumps(
        {
            "section": (result or {}).get("section") or section_key,
            "confidence": confidence,
            "patch": patch,
        },
        ensure_ascii=False,
    )

    train_messages = [
        {
            "role": "system",
            "content": system_prompt
            or (
                "You are the Orderly Affairs document fill brain. "
                "Return JSON only for the target section fields."
            ),
        },
        {
            "role": "user",
            "content": (user_prompt or text)[:MAX_PROMPT_STORE],
        },
        {
            "role": "assistant",
            "content": assistant_json,
        },
    ]

    meta = extract_meta or {}
    return {
        "schema_version": SKILL_SCHEMA_VERSION,
        "user_id": str(user_id),
        "file_id": file_id,
        "section_key": str(section_key),
        "task": "section_field_fill",
        "ideology": ORDERLY_IDEOLOGY,
        "input": {
            "document_text": text[:MAX_TEXT_STORE],
            "mime_type": mime_type,
            "extract_method": meta.get("method"),
            "quality_score": meta.get("quality_score"),
            "llm_input": meta.get("llm_input") or meta.get("gemini_input") or "text",
            "read_source": meta.get("read_source") or "system",
            "field_catalog_keys": catalog_keys[:200],
        },
        "teacher": {
            "provider": provider or "openai",
            "model": model or "gpt-4o-mini",
            "role": "current_production_brain",
        },
        "decision_trace": {
            "confidence": confidence,
            "classification": classification,
            "few_shot_count": int(few_shot_count or 0),
            "usage": usage or {},
            "thinking_style": (
                "Extract concrete values from OCR text, align to catalog keys, "
                "omit unknowns, protect sensitive fields."
            ),
        },
        "output": {
            "section": (result or {}).get("section") or section_key,
            "confidence": confidence,
            "patch": patch,
        },
        # Flat fields kept for few-shot retrieval compatibility
        "document_text": text[:MAX_TEXT_STORE],
        "patch": patch,
        "confidence": confidence,
        "provider": provider,
        "model": model,
        "train": {
            "format": "chat_messages",
            "messages": train_messages,
        },
        "created_at": datetime.now(timezone.utc),
    }


async def record_successful_fill(
    *,
    user_id: str,
    section_key: str,
    document_text: str,
    patch: dict[str, Any] | None,
    confidence: Any = None,
    provider: str | None = None,
    model: str | None = None,
    file_id: str | None = None,
    mime_type: str | None = None,
    extract_meta: dict[str, Any] | None = None,
    classification: dict[str, Any] | None = None,
    field_catalog: list[dict] | None = None,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    few_shot_count: int = 0,
    usage: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    """Caller should gate on learning_enabled before scheduling this."""
    doc = build_skill_record(
        user_id=user_id,
        section_key=section_key,
        document_text=document_text,
        patch=patch,
        confidence=confidence,
        provider=provider,
        model=model,
        file_id=file_id,
        mime_type=mime_type,
        extract_meta=extract_meta,
        classification=classification,
        field_catalog=field_catalog,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        few_shot_count=few_shot_count,
        usage=usage,
        result=result,
    )
    if not doc:
        return

    try:
        await ai_skill_examples_collection.insert_one(doc)
        logger.info(
            "AI skill memory saved section=%s user=%s chars=%s schema=%s",
            section_key,
            user_id,
            len(str(doc.get("document_text") or "")),
            SKILL_SCHEMA_VERSION,
        )
    except Exception as error:
        logger.warning("Failed to save skill example: %s", repr(error))


async def fetch_few_shot_examples(
    *,
    user_id: str,
    section_key: str,
    document_text: str,
    limit: int = MAX_FEW_SHOT,
) -> list[dict[str, Any]]:
    if not learning_enabled():
        return []
    cursor = (
        ai_skill_examples_collection.find(
            {"user_id": str(user_id), "section_key": str(section_key)},
            {"document_text": 1, "patch": 1, "created_at": 1, "output": 1},
        )
        .sort("created_at", -1)
        .limit(40)
    )
    rows = await cursor.to_list(length=40)
    if not rows:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        score = _overlap_score(document_text, str(row.get("document_text") or ""))
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)

    picked: list[dict[str, Any]] = []
    for score, row in scored:
        if score < 0.04 and picked:
            continue
        picked.append(row)
        if len(picked) >= limit:
            break
    return picked


def format_few_shot_prompt(examples: list[dict[str, Any]]) -> str:
    if not examples:
        return ""
    blocks: list[str] = [
        "\n\nLEARNED EXAMPLES FROM PAST SUCCESSFUL FILLS "
        "(follow the same field mapping style):\n"
    ]
    for index, ex in enumerate(examples, start=1):
        text = str(ex.get("document_text") or "")[:MAX_FEW_SHOT_CHARS]
        patch = ex.get("patch") or (ex.get("output") or {}).get("patch") or {}
        blocks.append(
            f"\n--- Example {index} ---\n"
            f"DOCUMENT TEXT:\n{text}\n\n"
            f"CORRECT PATCH JSON:\n{patch}\n"
        )
    blocks.append(
        "\nUse the examples only as mapping guidance. "
        "Extract values from the CURRENT document text only.\n"
    )
    return "".join(blocks)


async def skill_stats_for_user(user_id: str) -> dict[str, Any]:
    pipeline = [
        {"$match": {"user_id": str(user_id)}},
        {
            "$group": {
                "_id": "$section_key",
                "count": {"$sum": 1},
                "last_at": {"$max": "$created_at"},
            }
        },
        {"$sort": {"count": -1}},
    ]
    rows = await ai_skill_examples_collection.aggregate(pipeline).to_list(length=100)
    total = sum(int(r.get("count") or 0) for r in rows)
    by_section = [
        {
            "section_key": r.get("_id"),
            "count": int(r.get("count") or 0),
            "last_at": r.get("last_at"),
        }
        for r in rows
    ]
    return {
        "total_examples": total,
        "by_section": by_section,
        "schema_version": SKILL_SCHEMA_VERSION,
    }


async def export_skill_examples_for_training(
    *,
    user_id: str,
    section_key: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Export skill rows ready for fine-tuning / eval of Orderly own model."""
    query: dict[str, Any] = {"user_id": str(user_id)}
    if section_key:
        query["section_key"] = str(section_key)

    cursor = (
        ai_skill_examples_collection.find(query)
        .sort("created_at", -1)
        .limit(limit)
    )
    rows = await cursor.to_list(length=limit)
    exported: list[dict[str, Any]] = []
    for row in rows:
        section = str(row.get("section_key") or "")
        train = row.get("train") if isinstance(row.get("train"), dict) else None
        messages = (train or {}).get("messages")
        if not messages:
            patch = row.get("patch") or (row.get("output") or {}).get("patch") or {}
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You extract Orderly Affairs section fields from document text. "
                        "Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": str(row.get("document_text") or ""),
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "section": section,
                            "confidence": row.get("confidence"),
                            "patch": patch,
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
        exported.append(
            {
                "schema_version": row.get("schema_version") or SKILL_SCHEMA_VERSION,
                "section_key": section,
                "task": row.get("task") or "section_field_fill",
                "ideology": row.get("ideology") or ORDERLY_IDEOLOGY,
                "input": row.get("input")
                or {
                    "document_text": row.get("document_text"),
                },
                "teacher": row.get("teacher")
                or {
                    "provider": row.get("provider"),
                    "model": row.get("model"),
                },
                "decision_trace": row.get("decision_trace"),
                "output": row.get("output")
                or {
                    "section": section,
                    "confidence": row.get("confidence"),
                    "patch": row.get("patch"),
                },
                "train": {"format": "chat_messages", "messages": messages},
                "created_at": row.get("created_at"),
            }
        )
    return exported
