# app/ai/skill_memory.py
"""
Day-to-day Orderly skill corpus (MongoDB `ai_skill_examples`).

NOT shown to vault owners in the UI. Runs silently on each successful
classify/fill when AI_LEARNING_ENABLED=true.

Distinct skills stored for the future own model:
  document_ocr_prepare  — OCR quality gate + Terra/Sol routing
  section_classify      — topic + section/subsection matching
  section_field_fill    — semantic field mapping + structured patch

See app/ai/SKILL_MEMORY.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from app.database import ai_skill_examples_collection

logger = logging.getLogger(__name__)

SKILL_SCHEMA_VERSION = "orderly_skill_v3"
MAX_TEXT_STORE = 50000
MAX_PROMPT_STORE = 40000
MAX_FEW_SHOT = 2
MAX_FEW_SHOT_CHARS = 3500

TASK_OCR = "document_ocr_prepare"
TASK_CLASSIFY = "section_classify"
TASK_FILL = "section_field_fill"

ORDERLY_IDEOLOGY = {
    "pipeline": (
        "document → OCR (primary) → quality gate → "
        "(good) clean OCR text → Sol  |  (bad) Terra vision text → Sol → "
        "backend validation → auto-fill or user review"
    ),
    "roles": {
        "ocr": "Primary document reader. Never skipped.",
        "terra": "Fallback visual reader only when OCR quality is bad. Text only.",
        "sol": (
            "Intelligence engine. Understands topic, section, labels, and values. "
            "Maps meaning onto exact schema keys. Never invents values."
        ),
        "backend": "Authoritative validation, confidence bands, existing-value protection.",
    },
    "goal": (
        "Map document wording into exact Orderly Affairs vault section fields "
        "so next-of-kin can find critical information."
    ),
    "decision_policy": [
        "Use ONLY facts present in the prepared document text.",
        "Classify by document content, not filename.",
        "Match sections by meaning (Homeowners Coverage → Insurance Policies).",
        "Match fields by meaning (Policy No / Polcy Numbor → policy_number).",
        "Map synonyms to the exact catalog field keys (never invent new keys).",
        "Keep distinct people and dates distinct (holder vs agent vs beneficiary; "
        "effective vs expiration vs renewal).",
        "Prefer dedicated fields over dumping into notes.",
        "Omit empty/null fields when possible.",
        "Never extract raw passwords, full SSN, or full payment card numbers.",
        "If multiple entities exist, keep one object per entity.",
        "When unsure, lower confidence rather than hallucinating.",
        "Existing user-entered values must not be silently overwritten.",
        "Confidence >= 0.95 may auto-fill after validation; 0.80–0.94 review; "
        "< 0.80 do not silently commit.",
    ],
    "privacy_rules": [
        "No passwords",
        "No full SSN",
        "No full card numbers (last4 only if schema asks)",
    ],
}

_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


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


def redact_skill_text(text: str) -> str:
    """Strip full SSN / PAN-like numbers from stored training text."""
    cleaned = _SSN_RE.sub("[SSN]", text or "")

    def _card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19:
            return "[CARD]"
        return match.group(0)

    return _CARD_RE.sub(_card, cleaned)


def _confidence_band(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"high", "medium", "low"}:
            return lowered
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "low"
    if score > 1.0:
        score = score / 100.0
    if score >= 0.95:
        return "high"
    if score >= 0.80:
        return "medium"
    return "low"


def catalog_skill_fields(field_catalog: list[dict] | None) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in field_catalog or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("field_key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        fields.append(
            {
                "key": key,
                "label": str(item.get("label") or "").strip(),
                "type": str(item.get("type") or "TextInput"),
                "description": str(
                    item.get("helperText")
                    or item.get("helper_text")
                    or item.get("description")
                    or ""
                ).strip()[:240],
            }
        )
        if len(fields) >= 200:
            break
    return fields


def summarize_patch(
    patch: dict[str, Any] | None,
    field_catalog: list[dict] | None = None,
) -> dict[str, Any]:
    subsections: list[str] = []
    filled: list[str] = []
    if isinstance(patch, dict):
        for sub_key, value in patch.items():
            subsections.append(str(sub_key))
            items = value if isinstance(value, list) else [value]
            for item in items:
                if not isinstance(item, dict):
                    continue
                for field_key, field_value in item.items():
                    if str(field_key).startswith("__"):
                        continue
                    if field_value in (None, "", [], {}):
                        continue
                    if isinstance(field_value, str) and not field_value.strip():
                        continue
                    filled.append(str(field_key))
    filled_unique = sorted(set(filled))
    catalog_keys = [item["key"] for item in catalog_skill_fields(field_catalog)]
    omitted = [key for key in catalog_keys if key not in set(filled_unique)]
    return {
        "subsections": subsections,
        "filled_field_keys": filled_unique,
        "omitted_field_keys": omitted[:80],
        "filled_count": len(filled_unique),
        "omitted_count": len(omitted),
    }


def compact_classification(classification: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(classification, dict):
        return {}
    additional: list[dict[str, Any]] = []
    for item in classification.get("additional_sections") or []:
        if not isinstance(item, dict):
            continue
        additional.append(
            {
                "section_key": item.get("section_key"),
                "confidence": item.get("confidence"),
                "data_summary": str(item.get("data_summary") or "")[:400],
            }
        )
        if len(additional) >= 12:
            break
    return {
        "best_section_key": classification.get("best_section_key"),
        "matches_requested_section": bool(
            classification.get("matches_requested_section")
        ),
        "confidence": classification.get("confidence"),
        "document_summary": str(classification.get("document_summary") or "")[:800],
        "additional_sections": additional,
    }


def build_ocr_behavior(extract_meta: dict[str, Any] | None) -> dict[str, Any]:
    meta = extract_meta if isinstance(extract_meta, dict) else {}
    quality = str(meta.get("quality") or "").strip().lower()
    if quality not in {"good", "bad"}:
        quality = "bad" if meta.get("needs_vision") else "good"
    terra = bool(meta.get("terra_invoked"))
    path = str(meta.get("pipeline_path") or ("ocr_terra_sol" if terra else "ocr_sol"))
    return {
        "source": meta.get("source") or "ocr",
        "method": meta.get("method") or meta.get("extract_method"),
        "quality": quality,
        "quality_score": meta.get("quality_score"),
        "needs_vision": bool(meta.get("needs_vision")),
        "terra_invoked": terra,
        "terra_pages": meta.get("terra_pages") or [],
        "pipeline_path": path,
        "source_method": meta.get("source_method") or ("terra_vision" if terra else "ocr"),
        "read_source": meta.get("read_source") or "system",
        "page_count": meta.get("page_count"),
    }


def build_decision_trace(
    *,
    task: str,
    section_key: str,
    subsection: str | None = None,
    confidence: Any = None,
    extract_meta: dict[str, Any] | None = None,
    classification: dict[str, Any] | None = None,
    patch: dict[str, Any] | None = None,
    field_catalog: list[dict] | None = None,
    few_shot_count: int = 0,
    usage: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ocr = build_ocr_behavior(extract_meta)
    classified = compact_classification(classification)
    fill = summarize_patch(patch, field_catalog)
    band = _confidence_band(
        (extract_meta or {}).get("confidence_band")
        or (result or {}).get("confidence")
        or confidence
        or classified.get("confidence")
    )
    detected_section = classified.get("best_section_key") or (
        (result or {}).get("section") if isinstance(result, dict) else None
    ) or section_key
    return {
        "task": task,
        "ocr": ocr,
        "classify": {
            "requested_section": section_key,
            "detected_section": detected_section,
            "matches_requested_section": classified.get("matches_requested_section"),
            "additional_sections": [
                item.get("section_key")
                for item in classified.get("additional_sections") or []
                if item.get("section_key")
            ],
            "document_summary": classified.get("document_summary"),
            "classification_confidence": classified.get("confidence"),
        },
        "fill": {
            "requested_subsection": subsection or (result or {}).get("subsection"),
            "scope": (result or {}).get("scope"),
            "filled_subsections": fill["subsections"],
            "filled_field_keys": fill["filled_field_keys"],
            "omitted_field_keys": fill["omitted_field_keys"],
            "filled_count": fill["filled_count"],
            "confidence": confidence,
            "confidence_band": band,
            "autofill_eligible": band == "high",
            "review_required": band != "high",
            "validation": "backend_authoritative",
            "existing_value_policy": "never_silent_overwrite",
        },
        "few_shot_count": int(few_shot_count or 0),
        "usage": usage or {},
        "thinking_style": (
            "OCR reads first. If OCR is bad, Terra reconstructs text only. "
            "Sol understands the whole text, detects topic and section, "
            "matches labels by meaning onto exact catalog keys, omits unknowns, "
            "and returns structured JSON. Backend validates before auto-fill."
        ),
    }


def _fill_system_prompt() -> str:
    return (
        "You are GPT-5.6 Sol, the Orderly Affairs document intelligence engine. "
        "You receive prepared document TEXT (from OCR or Terra vision). "
        "Detect the document topic, choose the correct vault section, "
        "understand labels even when misspelled or worded differently, "
        "and map evidence-based values onto exact catalog field keys. "
        "Never invent values. Return JSON only."
    )


def _classify_system_prompt() -> str:
    return (
        "You are GPT-5.6 Sol classifying an Orderly Affairs vault document. "
        "Read the prepared text and choose the correct existing section by meaning, "
        "not filename. List additional sections only when the document truly "
        "belongs there. Never invent facts. Return JSON only."
    )


def _ocr_system_prompt() -> str:
    return (
        "You route Orderly document reading. OCR is primary. "
        "If OCR quality is good, send clean text to Sol. "
        "If OCR quality is bad, use Terra vision to reconstruct text, then Sol. "
        "Return JSON describing the routing decision."
    )


def _catalog_prompt_block(fields: list[dict[str, str]]) -> str:
    if not fields:
        return ""
    lines = ["Field catalog (map document labels onto these exact keys):"]
    for item in fields[:80]:
        line = f"- {item['key']}"
        if item.get("label"):
            line += f' ("{item["label"]}")'
        if item.get("description"):
            line += f" — {item['description']}"
        lines.append(line)
    return "\n".join(lines)


def _base_record(
    *,
    user_id: str,
    section_key: str,
    task: str,
    document_text: str,
    file_id: str | None,
    mime_type: str | None,
    provider: str | None,
    model: str | None,
    train_messages: list[dict[str, str]],
    decision_trace: dict[str, Any],
    output: dict[str, Any],
    field_catalog: list[dict] | None = None,
    patch: dict[str, Any] | None = None,
    confidence: Any = None,
) -> dict[str, Any]:
    text = redact_skill_text((document_text or "").strip())[:MAX_TEXT_STORE]
    fields = catalog_skill_fields(field_catalog)
    ocr = (decision_trace.get("ocr") or {}) if isinstance(decision_trace, dict) else {}
    return {
        "schema_version": SKILL_SCHEMA_VERSION,
        "user_id": str(user_id),
        "file_id": file_id,
        "section_key": str(section_key),
        "task": task,
        "ideology": ORDERLY_IDEOLOGY,
        "input": {
            "document_text": text,
            "mime_type": mime_type,
            "extract_method": ocr.get("method"),
            "quality_score": ocr.get("quality_score"),
            "ocr_quality": ocr.get("quality"),
            "pipeline_path": ocr.get("pipeline_path"),
            "terra_invoked": ocr.get("terra_invoked"),
            "llm_input": "text",
            "read_source": ocr.get("read_source") or "system",
            "field_catalog_keys": [item["key"] for item in fields],
            "field_catalog": fields,
        },
        "behaviors": decision_trace,
        "teacher": {
            "provider": provider or "openai",
            "model": model or "gpt-5.6-sol",
            "role": "terra" if task == TASK_OCR and ocr.get("terra_invoked") else "sol",
            "sol_model": "gpt-5.6-sol",
            "terra_model": "gpt-5.6-terra",
        },
        "decision_trace": decision_trace,
        "output": output,
        "document_text": text,
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


def build_ocr_skill_record(
    *,
    user_id: str,
    section_key: str,
    document_text: str,
    extract_meta: dict[str, Any] | None,
    file_id: str | None = None,
    mime_type: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    meta = extract_meta if isinstance(extract_meta, dict) else {}
    if not meta:
        return None
    ocr = build_ocr_behavior(meta)
    text = redact_skill_text((document_text or "").strip())[:MAX_TEXT_STORE]
    if len(text) < 20 and not ocr.get("terra_invoked"):
        return None
    output = {
        "quality": ocr.get("quality"),
        "pipeline_path": ocr.get("pipeline_path"),
        "terra_invoked": ocr.get("terra_invoked"),
        "source_method": ocr.get("source_method"),
        "needs_vision": ocr.get("needs_vision"),
    }
    train_messages = [
        {"role": "system", "content": _ocr_system_prompt()},
        {
            "role": "user",
            "content": (
                f"MIME: {mime_type or 'unknown'}\n"
                f"OCR method: {ocr.get('method')}\n"
                f"OCR quality_score: {ocr.get('quality_score')}\n"
                f"Prepared text preview:\n{text[:8000]}"
            )[:MAX_PROMPT_STORE],
        },
        {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)},
    ]
    return _base_record(
        user_id=user_id,
        section_key=section_key,
        task=TASK_OCR,
        document_text=text,
        file_id=file_id,
        mime_type=mime_type,
        provider=provider,
        model=model,
        train_messages=train_messages,
        decision_trace=build_decision_trace(
            task=TASK_OCR,
            section_key=section_key,
            extract_meta=meta,
        ),
        output=output,
        confidence=ocr.get("quality_score"),
    )


def build_classify_skill_record(
    *,
    user_id: str,
    requested_section_key: str,
    document_text: str,
    classification: dict[str, Any] | None,
    extract_meta: dict[str, Any] | None = None,
    file_id: str | None = None,
    mime_type: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    text = redact_skill_text((document_text or "").strip())
    if len(text) < 40:
        return None
    classified = compact_classification(classification)
    if not classified.get("best_section_key"):
        return None
    output = classified
    train_messages = [
        {"role": "system", "content": _classify_system_prompt()},
        {
            "role": "user",
            "content": (
                f"Requested section: {requested_section_key}\n"
                "Classify this prepared document text.\n\n"
                f"{text[:MAX_PROMPT_STORE]}"
            )[:MAX_PROMPT_STORE],
        },
        {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)},
    ]
    return _base_record(
        user_id=user_id,
        section_key=str(classified.get("best_section_key") or requested_section_key),
        task=TASK_CLASSIFY,
        document_text=text,
        file_id=file_id,
        mime_type=mime_type,
        provider=provider,
        model=model,
        train_messages=train_messages,
        decision_trace=build_decision_trace(
            task=TASK_CLASSIFY,
            section_key=requested_section_key,
            confidence=classified.get("confidence"),
            extract_meta=extract_meta,
            classification=classification,
            usage=usage,
        ),
        output=output,
        confidence=classified.get("confidence"),
    )


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
    subsection: str | None = None,
) -> dict[str, Any] | None:
    del user_prompt  # never store the giant live prompt; train on compact text
    text = redact_skill_text((document_text or "").strip())
    if len(text) < 40:
        return None
    if not isinstance(patch, dict) or not patch:
        return None

    fields = catalog_skill_fields(field_catalog)
    fill = summarize_patch(patch, field_catalog)
    assistant_json = json.dumps(
        {
            "section": (result or {}).get("section") or section_key,
            "subsection": subsection
            or (result or {}).get("subsection")
            or (fill["subsections"][0] if fill["subsections"] else None),
            "scope": (result or {}).get("scope") or "section",
            "confidence": confidence,
            "patch": patch,
        },
        ensure_ascii=False,
    )
    train_user = (
        f"Target section: {section_key}\n"
        f"Requested subsection: {subsection or (result or {}).get('subsection') or 'FULL_SECTION'}\n"
        f"{_catalog_prompt_block(fields)}\n\n"
        "Prepared document text. Extract only supported values. "
        "Use exact catalog keys. Never invent.\n\n"
        f"{text}"
    )[:MAX_PROMPT_STORE]
    train_messages = [
        {"role": "system", "content": (system_prompt or _fill_system_prompt())[:8000]},
        {"role": "user", "content": train_user},
        {"role": "assistant", "content": assistant_json},
    ]
    output = {
        "section": (result or {}).get("section") or section_key,
        "subsection": subsection or (result or {}).get("subsection"),
        "scope": (result or {}).get("scope"),
        "confidence": confidence,
        "patch": patch,
        "filled_field_keys": fill["filled_field_keys"],
        "filled_subsections": fill["subsections"],
    }
    return _base_record(
        user_id=user_id,
        section_key=section_key,
        task=TASK_FILL,
        document_text=text,
        file_id=file_id,
        mime_type=mime_type,
        provider=provider,
        model=model,
        train_messages=train_messages,
        decision_trace=build_decision_trace(
            task=TASK_FILL,
            section_key=section_key,
            subsection=subsection,
            confidence=confidence,
            extract_meta=extract_meta,
            classification=classification,
            patch=patch,
            field_catalog=field_catalog,
            few_shot_count=few_shot_count,
            usage=usage,
            result=result,
        ),
        output=output,
        field_catalog=field_catalog,
        patch=patch,
        confidence=confidence,
    )


async def _insert_skill(doc: dict[str, Any] | None) -> None:
    if not doc:
        return
    try:
        await ai_skill_examples_collection.insert_one(doc)
        logger.info(
            "AI skill saved task=%s section=%s user=%s path=%s fields=%s schema=%s",
            doc.get("task"),
            doc.get("section_key"),
            doc.get("user_id"),
            ((doc.get("behaviors") or {}).get("ocr") or {}).get("pipeline_path"),
            ((doc.get("behaviors") or {}).get("fill") or {}).get("filled_count"),
            SKILL_SCHEMA_VERSION,
        )
    except Exception as error:
        logger.warning("Failed to save skill example: %s", repr(error))


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
    subsection: str | None = None,
    record_ocr: bool = True,
    record_classify: bool = False,
) -> None:
    """Caller should gate on learning_enabled before scheduling this."""
    fill_doc = build_skill_record(
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
        subsection=subsection,
    )
    await _insert_skill(fill_doc)

    if record_ocr:
        await _insert_skill(
            build_ocr_skill_record(
                user_id=user_id,
                section_key=section_key,
                document_text=document_text,
                extract_meta=extract_meta,
                file_id=file_id,
                mime_type=mime_type,
                provider=provider,
                model=model,
            )
        )
    if record_classify:
        await record_classification_skill(
            user_id=user_id,
            requested_section_key=section_key,
            document_text=document_text,
            classification=classification,
            extract_meta=extract_meta,
            file_id=file_id,
            mime_type=mime_type,
            provider=provider,
            model=model,
            usage=usage,
        )


async def record_classification_skill(
    *,
    user_id: str,
    requested_section_key: str,
    document_text: str,
    classification: dict[str, Any] | None,
    extract_meta: dict[str, Any] | None = None,
    file_id: str | None = None,
    mime_type: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    usage: dict[str, Any] | None = None,
) -> None:
    await _insert_skill(
        build_classify_skill_record(
            user_id=user_id,
            requested_section_key=requested_section_key,
            document_text=document_text,
            classification=classification,
            extract_meta=extract_meta,
            file_id=file_id,
            mime_type=mime_type,
            provider=provider,
            model=model,
            usage=usage,
        )
    )


def _classify_meta_from_context() -> dict[str, Any]:
    try:
        from app.ai.llm_context import get_llm_settings

        meta = get_llm_settings().get("last_classify_meta")
        return dict(meta) if isinstance(meta, dict) else {}
    except Exception:
        return {}


def schedule_classification_skill(**kwargs: Any) -> None:
    if not learning_enabled():
        return
    if not kwargs.get("extract_meta"):
        kwargs["extract_meta"] = _classify_meta_from_context()
    prepared = str((kwargs.get("extract_meta") or {}).get("document_text") or "")
    incoming = str(kwargs.get("document_text") or "")
    if len(prepared) > len(incoming):
        kwargs["document_text"] = prepared
    elif not incoming:
        kwargs["document_text"] = prepared
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(record_classification_skill(**kwargs))


def schedule_successful_fill(**kwargs: Any) -> None:
    if not learning_enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(record_successful_fill(**kwargs))


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
            {
                "user_id": str(user_id),
                "section_key": str(section_key),
                "$or": [
                    {"task": TASK_FILL},
                    {"task": {"$exists": False}},
                ],
            },
            {
                "document_text": 1,
                "patch": 1,
                "created_at": 1,
                "output": 1,
                "behaviors": 1,
            },
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
        fill = ((ex.get("behaviors") or {}).get("fill") or {})
        keys = fill.get("filled_field_keys") or []
        subs = fill.get("filled_subsections") or []
        blocks.append(
            f"\n--- Example {index} ---\n"
            f"DOCUMENT TEXT:\n{text}\n\n"
            f"SUBSECTIONS: {subs}\n"
            f"FILLED KEYS: {keys}\n"
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
                "_id": {"section": "$section_key", "task": "$task"},
                "count": {"$sum": 1},
                "last_at": {"$max": "$created_at"},
            }
        },
        {"$sort": {"count": -1}},
    ]
    rows = await ai_skill_examples_collection.aggregate(pipeline).to_list(length=200)
    total = sum(int(r.get("count") or 0) for r in rows)
    by_section: dict[str, int] = {}
    by_task: dict[str, int] = {}
    section_rows: list[dict[str, Any]] = []
    for row in rows:
        key = row.get("_id") if isinstance(row.get("_id"), dict) else {}
        section = key.get("section") if isinstance(key, dict) else row.get("_id")
        task = (key.get("task") if isinstance(key, dict) else None) or TASK_FILL
        count = int(row.get("count") or 0)
        by_section[str(section)] = by_section.get(str(section), 0) + count
        by_task[str(task)] = by_task.get(str(task), 0) + count
        section_rows.append(
            {
                "section_key": section,
                "task": task,
                "count": count,
                "last_at": row.get("last_at"),
            }
        )
    return {
        "total_examples": total,
        "by_section": [
            {"section_key": section, "count": count}
            for section, count in sorted(by_section.items(), key=lambda item: -item[1])
        ],
        "by_task": [
            {"task": task, "count": count}
            for task, count in sorted(by_task.items(), key=lambda item: -item[1])
        ],
        "by_section_task": section_rows,
        "schema_version": SKILL_SCHEMA_VERSION,
        "skills": [TASK_OCR, TASK_CLASSIFY, TASK_FILL],
    }


async def export_skill_examples_for_training(
    *,
    user_id: str,
    section_key: str | None = None,
    task: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Export skill rows ready for fine-tuning / eval of Orderly own model."""
    query: dict[str, Any] = {"user_id": str(user_id)}
    if section_key:
        query["section_key"] = str(section_key)
    if task:
        query["task"] = str(task)

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
                    "content": _fill_system_prompt(),
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
                "task": row.get("task") or TASK_FILL,
                "ideology": row.get("ideology") or ORDERLY_IDEOLOGY,
                "input": row.get("input")
                or {
                    "document_text": row.get("document_text"),
                },
                "behaviors": row.get("behaviors") or row.get("decision_trace"),
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
