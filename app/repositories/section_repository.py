import hashlib
import json
from datetime import datetime

from app.database import section_data_collection
from app.notifications.section_update_notifications import (
    notify_immediate_access_on_section_update,
)
from app.security.section_crypto import decrypt_section_data


def _content_fingerprint(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str).encode("utf-8"),
    ).hexdigest()


def _fingerprint_from_encrypted(
    owner_id: str,
    section_id: str,
    encrypted_data: str,
) -> str:
    return _content_fingerprint(
        decrypt_section_data(owner_id, section_id, encrypted_data),
    )


def _is_effectively_empty(value) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return True
    if isinstance(value, dict):
        return all(_is_effectively_empty(v) for v in value.values())
    if isinstance(value, list):
        return all(_is_effectively_empty(v) for v in value)
    return False


class SectionRepository:

    @staticmethod
    async def get(owner_id: str, section_id: str):
        return await section_data_collection.find_one({
            "owner_id": owner_id,
            "section_id": section_id
        })

    @staticmethod
    async def upsert(
        owner_id: str,
        section_id: str,
        section_key: str,
        encrypted_data: str,
        subsections: list[str],
    ):
        existing = await SectionRepository.get(owner_id, section_id)
        new_fingerprint = _fingerprint_from_encrypted(
            owner_id,
            section_id,
            encrypted_data,
        )
        new_data = decrypt_section_data(owner_id, section_id, encrypted_data)

        old_fingerprint = None
        if existing:
            old_fingerprint = existing.get("content_fingerprint")
            if not old_fingerprint and existing.get("encrypted_data"):
                old_fingerprint = _fingerprint_from_encrypted(
                    owner_id,
                    section_id,
                    existing["encrypted_data"],
                )

        if old_fingerprint == new_fingerprint:
            return

        now = datetime.utcnow()

        await section_data_collection.update_one(
            {"owner_id": owner_id, "section_id": section_id},
            {
                "$set": {
                    "section_key": section_key,
                    "encrypted_data": encrypted_data,
                    "content_fingerprint": new_fingerprint,
                    "encryption_version": 2,
                    "subsections": subsections,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "created_at": now,
                },
            },
            upsert=True,
        )

        if _is_effectively_empty(new_data):
            return

        try:
            await notify_immediate_access_on_section_update(owner_id, section_id)
        except Exception as exc:
            print("⚠️ Section update notification dispatch failed:", section_id, exc)

    @staticmethod
    async def delete(owner_id: str, section_id: str):
        await section_data_collection.delete_one({
            "owner_id": owner_id,
            "section_id": section_id
        })
