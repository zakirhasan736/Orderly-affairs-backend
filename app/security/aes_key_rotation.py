"""Re-encrypt at-rest ciphertext under the current AES_256_KEY.

Use during AES key rotation after setting:
  AES_256_KEY=<new key>
  AES_256_KEY_PREVIOUS=<old key>

decrypt_data() accepts either key; this module rewrites every known encrypted
field so ciphertext is produced with the new key. After a clean run, remove
AES_256_KEY_PREVIOUS.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.ai.ai_extract_crypto import encrypt_extracted_text, read_extracted_text
from app.database import (
    ai_documents_collection,
    db,
    kits_collection,
    messageofnextkin_collection,
    pending_signup_collection,
    section_data_collection,
    users_collection,
)
from app.security.checklist_crypto import load_checklist_items, prepare_checklist_for_storage
from app.security.crypto import has_previous_aes_key, is_encrypted_payload
from app.security.kit_data_crypto import (
    load_kit_document,
    prepare_kit_section_for_storage,
    prepare_kit_subsection_for_storage,
)
from app.security.message_crypto import load_message, prepare_message_for_storage
from app.security.nextkin_profile_crypto import (
    load_nextkin_profile,
    prepare_nextkin_profile_for_storage,
)
from app.security.nok_letter_crypto import load_nok_letter, prepare_nok_letter_for_storage
from app.security.section_crypto import decrypt_section_data, encrypt_section_data
from app.security.totp_crypto import (
    decrypt_admin_totp_value,
    decrypt_totp_value,
    encrypt_admin_totp_value,
    encrypt_totp_value,
)

nok_letters_collection = db["nok_letters"]
letters_collection = db["letters"]


def _bump(stats: dict[str, int], key: str, n: int = 1) -> None:
    stats[key] = stats.get(key, 0) + n


async def reencrypt_vault_sections(*, dry_run: bool = False) -> dict[str, int]:
    stats = {"scanned": 0, "rewritten": 0, "failed": 0, "skipped": 0}
    async for section in section_data_collection.find(
        {"encrypted_data": {"$exists": True, "$nin": [None, ""]}}
    ):
        _bump(stats, "scanned")
        owner_id = str(section.get("owner_id") or "")
        section_id = str(section.get("section_id") or "")
        blob = section.get("encrypted_data")
        if not owner_id or not section_id or not blob:
            _bump(stats, "skipped")
            continue
        try:
            plaintext = decrypt_section_data(owner_id, section_id, blob)
            new_blob = encrypt_section_data(owner_id, section_id, plaintext)
            if new_blob == blob:
                _bump(stats, "skipped")
                continue
            if not dry_run:
                await section_data_collection.update_one(
                    {"_id": section["_id"]},
                    {
                        "$set": {
                            "encrypted_data": new_blob,
                            "encryption_version": 2,
                            "aes_key_rotated_at": datetime.utcnow(),
                            "updated_at": datetime.utcnow(),
                        }
                    },
                )
            _bump(stats, "rewritten")
        except Exception:
            _bump(stats, "failed")
    return stats


async def reencrypt_messages(*, dry_run: bool = False) -> dict[str, int]:
    stats = {"scanned": 0, "rewritten": 0, "failed": 0, "skipped": 0}
    async for doc in messageofnextkin_collection.find(
        {"encrypted_payload": {"$exists": True, "$nin": [None, ""]}}
    ):
        _bump(stats, "scanned")
        try:
            loaded = load_message(doc) or {}
            stored = prepare_message_for_storage(loaded)
            new_blob = stored.get("encrypted_payload")
            if not new_blob or new_blob == doc.get("encrypted_payload"):
                _bump(stats, "skipped")
                continue
            if not dry_run:
                await messageofnextkin_collection.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            **stored,
                            "aes_key_rotated_at": datetime.utcnow(),
                            "updated_at": datetime.utcnow(),
                        }
                    },
                )
            _bump(stats, "rewritten")
        except Exception:
            _bump(stats, "failed")
    return stats


async def _reencrypt_letter_collection(collection, *, dry_run: bool) -> dict[str, int]:
    stats = {"scanned": 0, "rewritten": 0, "failed": 0, "skipped": 0}
    async for doc in collection.find(
        {"encrypted_payload": {"$exists": True, "$nin": [None, ""]}}
    ):
        _bump(stats, "scanned")
        try:
            loaded = load_nok_letter(doc) or {}
            stored = prepare_nok_letter_for_storage(loaded)
            new_blob = stored.get("encrypted_payload")
            if not new_blob or new_blob == doc.get("encrypted_payload"):
                _bump(stats, "skipped")
                continue
            if not dry_run:
                await collection.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            **stored,
                            "aes_key_rotated_at": datetime.utcnow(),
                            "updated_at": datetime.utcnow(),
                        }
                    },
                )
            _bump(stats, "rewritten")
        except Exception:
            _bump(stats, "failed")
    return stats


async def reencrypt_nok_letters(*, dry_run: bool = False) -> dict[str, int]:
    return await _reencrypt_letter_collection(nok_letters_collection, dry_run=dry_run)


async def reencrypt_legacy_letters(*, dry_run: bool = False) -> dict[str, int]:
    return await _reencrypt_letter_collection(letters_collection, dry_run=dry_run)


async def reencrypt_checklists(*, dry_run: bool = False) -> dict[str, int]:
    stats = {"scanned": 0, "rewritten": 0, "failed": 0, "skipped": 0}
    async for doc in kits_collection.find(
        {"encrypted_items": {"$exists": True, "$nin": [None, ""]}}
    ):
        _bump(stats, "scanned")
        try:
            owner_id = str(doc.get("owner_id") or "")
            nextkin_id = str(doc.get("nextkin_id") or "")
            section_id = str(doc.get("section_id") or "")
            items = load_checklist_items(doc)
            stored = prepare_checklist_for_storage(
                owner_id=owner_id,
                nextkin_id=nextkin_id,
                section_id=section_id,
                items=items,
            )
            if stored.get("encrypted_items") == doc.get("encrypted_items"):
                _bump(stats, "skipped")
                continue
            if not dry_run:
                await kits_collection.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            **stored,
                            "aes_key_rotated_at": datetime.utcnow(),
                        }
                    },
                )
            _bump(stats, "rewritten")
        except Exception:
            _bump(stats, "failed")
    return stats


async def reencrypt_kit_sections(*, dry_run: bool = False) -> dict[str, int]:
    stats = {"scanned": 0, "rewritten": 0, "failed": 0, "skipped": 0}
    async for kit in kits_collection.find({"sections": {"$exists": True}}):
        _bump(stats, "scanned")
        try:
            owner_id = str(kit.get("owner_id") or "")
            if not owner_id:
                _bump(stats, "skipped")
                continue
            loaded = load_kit_document(dict(kit)) or {}
            changed = False
            sections = loaded.get("sections") or []
            for section in sections:
                section_id = str(section.get("id") or "")
                data = section.get("data")
                if data is not None:
                    enc = prepare_kit_section_for_storage(owner_id, section_id, data)
                    section["encrypted_data"] = enc["encrypted_data"]
                    section["encryption_version"] = enc["encryption_version"]
                    section.pop("data", None)
                    changed = True
                for subsection in section.get("subsections") or []:
                    sub_id = str(subsection.get("id") or "")
                    sub_data = subsection.get("data")
                    if sub_data is not None:
                        enc = prepare_kit_subsection_for_storage(
                            owner_id, sub_id, sub_data
                        )
                        subsection["encrypted_data"] = enc["encrypted_data"]
                        subsection["encryption_version"] = enc["encryption_version"]
                        subsection.pop("data", None)
                        changed = True
            if not changed:
                _bump(stats, "skipped")
                continue
            if not dry_run:
                await kits_collection.update_one(
                    {"_id": kit["_id"]},
                    {
                        "$set": {
                            "sections": sections,
                            "aes_key_rotated_at": datetime.utcnow(),
                            "updated_at": datetime.utcnow(),
                        }
                    },
                )
            _bump(stats, "rewritten")
        except Exception:
            _bump(stats, "failed")
    return stats


async def reencrypt_nextkin_profiles(*, dry_run: bool = False) -> dict[str, int]:
    stats = {"scanned": 0, "rewritten": 0, "failed": 0, "skipped": 0}
    async for doc in users_collection.find({"role": "nextkin"}):
        if not doc.get("encrypted_profile") and not any(
            doc.get(k)
            for k in (
                "card_storage_location",
                "key_bag_location",
                "documents_bag_location",
                "special_instructions",
            )
        ):
            continue
        _bump(stats, "scanned")
        try:
            loaded = load_nextkin_profile(doc) or doc
            stored = prepare_nextkin_profile_for_storage(loaded)
            if stored.get("encrypted_profile") == doc.get("encrypted_profile"):
                _bump(stats, "skipped")
                continue
            if not dry_run:
                await users_collection.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            **stored,
                            "aes_key_rotated_at": datetime.utcnow(),
                        }
                    },
                )
            _bump(stats, "rewritten")
        except Exception:
            _bump(stats, "failed")
    return stats


async def reencrypt_totp(*, dry_run: bool = False) -> dict[str, int]:
    stats = {"scanned": 0, "rewritten": 0, "failed": 0, "skipped": 0}

    async def _user_field(
        collection,
        field: str,
        *,
        pending: bool = False,
        admin: bool = False,
    ) -> None:
        async for doc in collection.find({field: {"$exists": True, "$nin": [None, ""]}}):
            _bump(stats, "scanned")
            value = str(doc.get(field) or "")
            if not is_encrypted_payload(value):
                _bump(stats, "skipped")
                continue
            email = str(doc.get("email") or "").strip().lower()
            if not email:
                _bump(stats, "skipped")
                continue
            try:
                if admin:
                    secret = decrypt_admin_totp_value(email, value)
                    new_blob = encrypt_admin_totp_value(email, secret)
                else:
                    secret = decrypt_totp_value(email, value, pending=pending)
                    new_blob = encrypt_totp_value(email, secret, pending=pending)
                if not secret or new_blob == value:
                    _bump(stats, "skipped")
                    continue
                if not dry_run:
                    await collection.update_one(
                        {"_id": doc["_id"]},
                        {"$set": {field: new_blob}},
                    )
                _bump(stats, "rewritten")
            except Exception:
                _bump(stats, "failed")

    await _user_field(users_collection, "totp_secret", pending=False)
    await _user_field(users_collection, "provisioned_secret", pending=True)
    await _user_field(users_collection, "admin_totp_secret_enc", admin=True)
    await _user_field(users_collection, "admin_totp_secret", admin=True)
    await _user_field(pending_signup_collection, "totp_secret", pending=True)
    await _user_field(pending_signup_collection, "provisioned_secret", pending=True)
    return stats


async def reencrypt_ai_extracts(*, dry_run: bool = False) -> dict[str, int]:
    stats = {"scanned": 0, "rewritten": 0, "failed": 0, "skipped": 0}
    async for doc in ai_documents_collection.find(
        {"extracted_text": {"$exists": True, "$nin": [None, ""]}}
    ):
        _bump(stats, "scanned")
        user_id = str(doc.get("user_id") or doc.get("owner_id") or "")
        file_id = str(doc.get("_id") or "")
        try:
            plaintext = read_extracted_text(doc)
            if not plaintext:
                _bump(stats, "skipped")
                continue
            new_blob = encrypt_extracted_text(
                user_id=user_id,
                file_id=file_id,
                text=plaintext,
            )
            if new_blob == doc.get("extracted_text"):
                _bump(stats, "skipped")
                continue
            if not dry_run:
                await ai_documents_collection.update_one(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "extracted_text": new_blob,
                            "extracted_text_encrypted": True,
                            "aes_key_rotated_at": datetime.utcnow(),
                            "updated_at": datetime.utcnow(),
                        }
                    },
                )
            _bump(stats, "rewritten")
        except Exception:
            _bump(stats, "failed")
    return stats


async def run_aes_key_rotation(
    *,
    dry_run: bool = False,
    require_previous: bool = True,
) -> dict[str, Any]:
    if require_previous and not has_previous_aes_key():
        raise RuntimeError(
            "AES_256_KEY_PREVIOUS is not set. Set it to the old key before rotating."
        )

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "previous_key_loaded": has_previous_aes_key(),
    }
    result["vault_sections"] = await reencrypt_vault_sections(dry_run=dry_run)
    result["messages"] = await reencrypt_messages(dry_run=dry_run)
    result["nok_letters"] = await reencrypt_nok_letters(dry_run=dry_run)
    result["legacy_letters"] = await reencrypt_legacy_letters(dry_run=dry_run)
    result["checklists"] = await reencrypt_checklists(dry_run=dry_run)
    result["kit_sections"] = await reencrypt_kit_sections(dry_run=dry_run)
    result["nextkin_profiles"] = await reencrypt_nextkin_profiles(dry_run=dry_run)
    result["totp"] = await reencrypt_totp(dry_run=dry_run)
    result["ai_extracts"] = await reencrypt_ai_extracts(dry_run=dry_run)

    failed = sum(
        int((result.get(k) or {}).get("failed") or 0)
        for k in (
            "vault_sections",
            "messages",
            "nok_letters",
            "legacy_letters",
            "checklists",
            "kit_sections",
            "nextkin_profiles",
            "totp",
            "ai_extracts",
        )
    )
    result["total_failed"] = failed
    return result
