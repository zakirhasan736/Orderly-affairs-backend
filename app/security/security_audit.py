from __future__ import annotations

import logging

from app.database import (
    kits_collection,
    messageofnextkin_collection,
    section_data_collection,
    users_collection,
)
from app.security.checklist_crypto import load_checklist_items
from app.security.kit_data_crypto import load_kit_document
from app.security.message_crypto import load_message
from app.security.nextkin_profile_crypto import load_nextkin_profile
from app.security.nok_letter_crypto import load_nok_letter
from app.security.section_crypto import decrypt_section_data

logger = logging.getLogger(__name__)

nok_letters_collection = None


def _nok_letters():
    global nok_letters_collection
    if nok_letters_collection is None:
        from app.database import db
        nok_letters_collection = db["nok_letters"]
    return nok_letters_collection


async def _audit_collection(name: str, audit_fn) -> dict:
    try:
        result = await audit_fn()
        logger.info("Security audit %s ok: %s", name, result)
        return {"status": "ok", **result}
    except Exception as exc:
        logger.error("Security audit %s failed: %s", name, exc)
        return {"status": "error", "error": str(exc)}


async def audit_vault_sections() -> dict:
    checked = 0
    failed = 0
    async for section in section_data_collection.find({}):
        owner_id = str(section.get("owner_id") or "")
        section_id = str(section.get("section_id") or "")
        encrypted = section.get("encrypted_data")
        if not encrypted:
            failed += 1
            continue
        try:
            decrypt_section_data(owner_id, section_id, encrypted)
            checked += 1
        except Exception:
            failed += 1
    return {"checked": checked, "failed": failed}


async def audit_messages() -> dict:
    checked = 0
    failed = 0
    plain_title_left = 0
    async for doc in messageofnextkin_collection.find({"is_deleted": False}):
        if doc.get("title") is not None:
            plain_title_left += 1
        try:
            loaded = load_message(doc)
            if not loaded or not loaded.get("_id"):
                failed += 1
                continue
            checked += 1
        except Exception:
            failed += 1
    return {"checked": checked, "failed": failed, "plain_title_left": plain_title_left}


async def audit_nok_letters() -> dict:
    checked = 0
    failed = 0
    plaintext_left = 0
    async for doc in _nok_letters().find({}):
        if not doc.get("encrypted_payload") and doc.get("letter_opening"):
            plaintext_left += 1
        try:
            load_nok_letter(doc)
            checked += 1
        except Exception:
            failed += 1
    return {"checked": checked, "failed": failed, "plaintext_left": plaintext_left}


async def audit_nextkin_profiles() -> dict:
    checked = 0
    failed = 0
    plain_sensitive_left = 0
    async for doc in users_collection.find({"role": "nextkin"}):
        if any(doc.get(k) for k in (
            "card_storage_location",
            "key_bag_location",
            "documents_bag_location",
            "special_instructions",
        )):
            plain_sensitive_left += 1
        try:
            load_nextkin_profile(doc)
            checked += 1
        except Exception:
            failed += 1
    return {"checked": checked, "failed": failed, "plain_sensitive_left": plain_sensitive_left}


async def audit_checklists() -> dict:
    checked = 0
    failed = 0
    plain_items_left = 0
    async for doc in kits_collection.find({"section_id": {"$exists": True}}):
        if doc.get("items") is not None:
            plain_items_left += 1
        try:
            load_checklist_items(doc)
            checked += 1
        except Exception:
            failed += 1
    return {"checked": checked, "failed": failed, "plain_items_left": plain_items_left}


async def audit_kit_documents() -> dict:
    checked = 0
    failed = 0
    plain_section_data_left = 0
    async for kit in kits_collection.find({"sections": {"$exists": True}}):
        for section in kit.get("sections") or []:
            if section.get("data") is not None:
                plain_section_data_left += 1
        try:
            load_kit_document(kit)
            checked += 1
        except Exception:
            failed += 1
    return {
        "checked": checked,
        "failed": failed,
        "plain_section_data_left": plain_section_data_left,
    }


async def run_security_audit() -> dict:
    results = {
        "vault_sections": await _audit_collection("vault_sections", audit_vault_sections),
        "messages": await _audit_collection("messages", audit_messages),
        "nok_letters": await _audit_collection("nok_letters", audit_nok_letters),
        "nextkin_profiles": await _audit_collection("nextkin_profiles", audit_nextkin_profiles),
        "checklists": await _audit_collection("checklists", audit_checklists),
        "kit_documents": await _audit_collection("kit_documents", audit_kit_documents),
    }
    logger.info("Security audit finished: %s", results)
    return results
