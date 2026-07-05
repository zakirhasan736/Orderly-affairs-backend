from __future__ import annotations

from datetime import datetime
from typing import Any

from app.database import (
    db,
    kits_collection,
    messageofnextkin_collection,
    section_data_collection,
    users_collection,
)
from app.security.checklist_crypto import load_checklist_items, prepare_checklist_for_storage
from app.security.kit_data_crypto import (
    load_kit_document,
    prepare_kit_section_for_storage,
    prepare_kit_subsection_for_storage,
)
from app.security.message_crypto import load_message, prepare_message_for_storage
from app.security.nextkin_profile_crypto import load_nextkin_profile, prepare_nextkin_profile_for_storage
from app.security.nok_letter_crypto import load_nok_letter, prepare_nok_letter_for_storage
from app.security.section_crypto import decrypt_section_data, encrypt_section_data
from app.security.totp_migration import run_totp_encryption_migration

nok_letters_collection = db["nok_letters"]
letters_collection = db["letters"]


async def migrate_nok_letters() -> int:
    migrated = 0
    async for doc in nok_letters_collection.find({}):
        if doc.get("encrypted_payload"):
            continue

        has_sensitive = any(
            doc.get(key) is not None
            for key in (
                "letter_to",
                "letter_greeting",
                "letter_opening",
                "kit_description",
                "access_url",
                "login_credentials_text",
                "nok_phone",
                "password_card_location",
                "accessible_sections",
                "key_bag_info",
                "key_bag_location",
                "documents_bag_info",
                "documents_bag_location",
                "incomplete_kit_message",
                "closing_message",
                "letter_signature",
            )
        )
        if not has_sensitive:
            continue

        stored = prepare_nok_letter_for_storage(doc)
        await nok_letters_collection.update_one({"_id": doc["_id"]}, {"$set": stored})
        migrated += 1

    return migrated


async def migrate_legacy_letters_collection() -> int:
    migrated = 0
    async for doc in letters_collection.find({}):
        if doc.get("encrypted_payload"):
            continue

        stored = prepare_nok_letter_for_storage(doc)
        if not stored.get("encrypted_payload"):
            continue

        await letters_collection.update_one({"_id": doc["_id"]}, {"$set": stored})
        migrated += 1

    return migrated


async def migrate_messages() -> int:
    migrated = 0
    async for doc in messageofnextkin_collection.find({}):
        needs_title_migration = doc.get("title") is not None
        needs_encryption_version = doc.get("encryption_version") != 2

        if not needs_title_migration and not needs_encryption_version:
            payload = doc.get("encrypted_payload")
            if payload:
                continue

        stored = prepare_message_for_storage(doc)
        unset: dict[str, str] = {}
        for key in ("title", "subject", "content", "recipient"):
            if key in doc:
                unset[key] = ""

        update_doc: dict[str, Any] = {"$set": stored}
        if unset:
            update_doc["$unset"] = unset

        await messageofnextkin_collection.update_one({"_id": doc["_id"]}, update_doc)
        migrated += 1

    return migrated


async def migrate_checklists() -> int:
    migrated = 0
    async for doc in kits_collection.find({"section_id": {"$exists": True}}):
        if doc.get("encrypted_items"):
            continue
        if not doc.get("items"):
            continue

        owner_id = str(doc.get("owner_id") or "")
        nextkin_id = str(doc.get("nextkin_id") or "")
        section_id = str(doc.get("section_id") or "")
        items = load_checklist_items(doc)

        encrypted = prepare_checklist_for_storage(
            owner_id=owner_id,
            nextkin_id=nextkin_id,
            section_id=section_id,
            items=items,
        )
        await kits_collection.update_one(
            {"_id": doc["_id"]},
            {
                "$set": encrypted,
                "$unset": {"items": ""},
            },
        )
        migrated += 1

    return migrated


async def migrate_kit_sections() -> int:
    migrated = 0
    async for kit in kits_collection.find({"sections": {"$exists": True}}):
        owner_id = str(kit.get("owner_id") or "")
        if not owner_id:
            continue

        changed = False
        sections = kit.get("sections") or []
        for section in sections:
            section_id = str(section.get("id") or "")
            if section.get("data") is not None and not section.get("encrypted_data"):
                encrypted = prepare_kit_section_for_storage(
                    owner_id,
                    section_id,
                    section["data"],
                )
                section["encrypted_data"] = encrypted["encrypted_data"]
                section["encryption_version"] = encrypted["encryption_version"]
                section.pop("data", None)
                changed = True

            for subsection in section.get("subsections") or []:
                sub_id = str(subsection.get("id") or "")
                if subsection.get("data") is not None and not subsection.get("encrypted_data"):
                    encrypted = prepare_kit_subsection_for_storage(
                        owner_id,
                        sub_id,
                        subsection["data"],
                    )
                    subsection["encrypted_data"] = encrypted["encrypted_data"]
                    subsection["encryption_version"] = encrypted["encryption_version"]
                    subsection.pop("data", None)
                    changed = True

        if changed:
            await kits_collection.update_one(
                {"_id": kit["_id"]},
                {
                    "$set": {
                        "sections": sections,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
            migrated += 1

    return migrated


async def migrate_nextkin_profiles() -> int:
    migrated = 0
    async for doc in users_collection.find({"role": "nextkin"}):
        has_plain_sensitive = any(doc.get(key) for key in (
            "card_storage_location",
            "key_bag_location",
            "documents_bag_location",
            "special_instructions",
        ))
        if not has_plain_sensitive and doc.get("encrypted_profile"):
            continue

        stored = prepare_nextkin_profile_for_storage(doc)
        unset = {
            key: ""
            for key in (
                "card_storage_location",
                "key_bag_location",
                "documents_bag_location",
                "special_instructions",
            )
            if key in doc
        }
        update_doc: dict[str, Any] = {"$set": stored}
        if unset:
            update_doc["$unset"] = unset

        await users_collection.update_one({"_id": doc["_id"]}, update_doc)
        migrated += 1

    return migrated


async def rebind_section_encryption() -> int:
    migrated = 0
    async for section in section_data_collection.find({}):
        owner_id = str(section.get("owner_id") or "")
        section_id = str(section.get("section_id") or "")
        encrypted_data = section.get("encrypted_data")
        if not owner_id or not section_id or not encrypted_data:
            continue

        if section.get("encryption_version") == 2:
            continue

        try:
            plaintext = decrypt_section_data(owner_id, section_id, encrypted_data)
            rebound = encrypt_section_data(owner_id, section_id, plaintext)
            await section_data_collection.update_one(
                {"_id": section["_id"]},
                {
                    "$set": {
                        "encrypted_data": rebound,
                        "encryption_version": 2,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
            migrated += 1
        except Exception as exc:
            print(
                "Section rebind failed:",
                owner_id,
                section_id,
                type(exc).__name__,
                exc,
            )

    return migrated


async def _run_step(name: str, fn) -> int:
    try:
        return await fn()
    except Exception as exc:
        print(f"Encryption migration step {name} failed:", type(exc).__name__, exc)
        return 0


async def run_encryption_migration() -> dict[str, int]:
    results = {
        "nok_letters": await _run_step("nok_letters", migrate_nok_letters),
        "legacy_letters": await _run_step("legacy_letters", migrate_legacy_letters_collection),
        "messages": await _run_step("messages", migrate_messages),
        "checklists": await _run_step("checklists", migrate_checklists),
        "kit_sections": await _run_step("kit_sections", migrate_kit_sections),
        "nextkin_profiles": await _run_step("nextkin_profiles", migrate_nextkin_profiles),
        "vault_sections": await _run_step("vault_sections", rebind_section_encryption),
        "totp_secrets": await _run_step("totp_secrets", _migrate_totp_secrets),
    }
    print("Encryption-at-rest migration complete:", results)
    return results


async def _migrate_totp_secrets() -> int:
    result = await run_totp_encryption_migration()
    return result.get("users", 0) + result.get("pending_signups", 0)
