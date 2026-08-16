# app/ai/ai_brain_routes.py
"""
ADMIN ONLY — not mounted in main.py until admin panel ships.

Owners never see skill/training data in the vault UI. Fills still write to
MongoDB `ai_skill_examples` silently when AI_LEARNING_ENABLED=true.

When admin is ready: mount this router + add admin auth guard on all routes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.ai.ai_auth import get_current_owner, get_user_id
from app.ai.llm_generate import active_brain_info
from app.ai.skill_memory import (
    SKILL_SCHEMA_VERSION,
    export_skill_examples_for_training,
    learning_enabled,
    skill_stats_for_user,
)
from app.database import ai_brain_settings_collection

router = APIRouter(prefix="/ai/brain", tags=["AI Brain"])


class BrainSettingsUpdate(BaseModel):
    learning_enabled: bool = True


async def get_brain_settings_for_user(user_id: str) -> dict[str, Any]:
    brain = active_brain_info()
    learn = learning_enabled()
    doc = await ai_brain_settings_collection.find_one({"user_id": str(user_id)})
    if doc and "learning_enabled" in doc:
        learn = bool(doc.get("learning_enabled"))

    return {
        "provider": brain["provider"],
        "model": brain["model"],
        "configured": brain["configured"],
        "mode": brain["mode"],
        "notes": brain["notes"],
        "learning_enabled": learn,
    }


@router.get("/settings")
async def get_settings(current_user=Depends(get_current_owner)):
    user_id = get_user_id(current_user)
    settings = await get_brain_settings_for_user(user_id)
    stats = await skill_stats_for_user(user_id)
    return {
        **settings,
        "skill": stats,
        "skill_schema_version": SKILL_SCHEMA_VERSION,
        "how_learning_works": (
            "Every successful document run stores three skills: OCR routing, "
            "section classification, and field fill (catalog keys, subsections, "
            "confidence, and chat train messages). Export this corpus later to "
            "train Orderly's own model to do the same job as GPT-5.6 Sol."
        ),
        "future_own_model": {
            "switch": "Set AI_PROVIDER=own, OWN_MODEL_BASE_URL, OWN_MODEL_NAME",
            "data": "Export skill examples from /ai/brain/skill-export",
            "schema": SKILL_SCHEMA_VERSION,
        },
    }


@router.put("/settings")
async def put_settings(
    payload: BrainSettingsUpdate,
    current_user=Depends(get_current_owner),
):
    user_id = get_user_id(current_user)
    brain = active_brain_info()
    doc = {
        "user_id": str(user_id),
        "provider": brain["provider"],
        "model": brain["model"],
        "learning_enabled": bool(payload.learning_enabled),
        "updated_at": datetime.now(timezone.utc),
    }
    await ai_brain_settings_collection.update_one(
        {"user_id": str(user_id)},
        {"$set": doc},
        upsert=True,
    )
    stats = await skill_stats_for_user(user_id)
    return {
        **doc,
        "configured": brain["configured"],
        "mode": brain["mode"],
        "notes": brain["notes"],
        "skill": stats,
        "skill_schema_version": SKILL_SCHEMA_VERSION,
    }


@router.get("/skill-export")
async def skill_export(
    current_user=Depends(get_current_owner),
    limit: int = Query(default=500, ge=1, le=5000),
    section_key: str | None = Query(default=None),
    task: str | None = Query(default=None),
):
    """Export skill examples for future Orderly model fine-tuning / eval."""
    user_id = get_user_id(current_user)
    rows = await export_skill_examples_for_training(
        user_id=user_id,
        section_key=section_key,
        task=task,
        limit=limit,
    )
    return {
        "format": SKILL_SCHEMA_VERSION,
        "count": len(rows),
        "examples": rows,
        "training_hint": (
            "Fine-tune an OpenAI-compatible chat model on train.messages "
            "grouped by task: document_ocr_prepare, section_classify, "
            "section_field_fill. Then set AI_PROVIDER=own to serve it."
        ),
    }
