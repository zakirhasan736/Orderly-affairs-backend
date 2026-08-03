import hashlib
import json
from datetime import datetime
from typing import Any

from app.database import section_data_collection
from app.notifications.section_update_notifications import (
    notify_immediate_access_on_section_update,
)
from app.security.section_crypto import decrypt_section_data
from app.utils.empty import is_effectively_empty

# Who changed which section (owner + family collaborators)
section_footprints_collection = section_data_collection.database["section_footprints"]


def _content_fingerprint(data: Any) -> str:
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
    return is_effectively_empty(value)


def _as_dict(data: Any) -> dict:
    return data if isinstance(data, dict) else {}


def _changed_scopes(old_data: Any, new_data: Any) -> list[str]:
    """
    Return changed subsection ids and item scopes (e.g. '5A', '5A:0').
    """
    old = _as_dict(old_data)
    new = _as_dict(new_data)
    keys = set(old.keys()) | set(new.keys())
    changed: list[str] = []
    for key in keys:
        old_val = old.get(key)
        new_val = new.get(key)
        if _content_fingerprint(old_val) == _content_fingerprint(new_val):
            continue
        changed.append(str(key))
        if isinstance(old_val, list) or isinstance(new_val, list):
            old_list = old_val if isinstance(old_val, list) else []
            new_list = new_val if isinstance(new_val, list) else []
            max_len = max(len(old_list), len(new_list))
            for idx in range(max_len):
                left = old_list[idx] if idx < len(old_list) else None
                right = new_list[idx] if idx < len(new_list) else None
                if _content_fingerprint(left) != _content_fingerprint(right):
                    changed.append(f"{key}:{idx}")
        elif isinstance(old_val, dict) or isinstance(new_val, dict):
            old_map = old_val if isinstance(old_val, dict) else {}
            new_map = new_val if isinstance(new_val, dict) else {}
            nested = set(old_map.keys()) | set(new_map.keys())
            for nested_key in nested:
                if _content_fingerprint(old_map.get(nested_key)) != _content_fingerprint(
                    new_map.get(nested_key)
                ):
                    changed.append(f"{key}.{nested_key}")
    return changed


def _actor_payload(actor_meta: dict, source: str, now: datetime) -> dict:
    return {
        "user_id": actor_meta.get("user_id") or "",
        "full_name": actor_meta.get("full_name") or "Unknown",
        "email": actor_meta.get("email") or "",
        "role": actor_meta.get("role") or "owner",
        "portal_role": actor_meta.get("portal_role"),
        "portal_role_label": actor_meta.get("portal_role_label") or "Owner",
        "source": source,
        "at": now,
    }


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
        actor: dict | None = None,
        source: str = "manual",
    ):
        existing = await SectionRepository.get(owner_id, section_id)
        new_fingerprint = _fingerprint_from_encrypted(
            owner_id,
            section_id,
            encrypted_data,
        )
        new_data = decrypt_section_data(owner_id, section_id, encrypted_data)

        old_fingerprint = None
        old_data: Any = {}
        if existing:
            old_fingerprint = existing.get("content_fingerprint")
            if not old_fingerprint and existing.get("encrypted_data"):
                old_fingerprint = _fingerprint_from_encrypted(
                    owner_id,
                    section_id,
                    existing["encrypted_data"],
                )
            if existing.get("encrypted_data"):
                try:
                    old_data = decrypt_section_data(
                        owner_id,
                        section_id,
                        existing["encrypted_data"],
                    )
                except Exception:
                    old_data = {}

        if old_fingerprint == new_fingerprint:
            return

        now = datetime.utcnow()
        actor_meta = actor or {}
        last_updated_by = _actor_payload(actor_meta, source, now)
        changed_scopes = _changed_scopes(old_data, new_data)
        if not changed_scopes:
            changed_scopes = [str(section_id)]

        subsection_updates = (
            dict(existing.get("subsection_updates") or {}) if existing else {}
        )
        for scope in changed_scopes:
            subsection_updates[str(scope)] = {
                "actor": last_updated_by,
                "updated_at": now,
                "source": source,
            }

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
                    "last_updated_by": last_updated_by,
                    "subsection_updates": subsection_updates,
                },
                "$setOnInsert": {
                    "created_at": now,
                },
            },
            upsert=True,
        )

        try:
            await section_footprints_collection.insert_one(
                {
                    "owner_id": owner_id,
                    "section_id": str(section_id),
                    "section_key": section_key,
                    "actor": last_updated_by,
                    "source": source,
                    "scopes": changed_scopes,
                    "created_at": now,
                }
            )
        except Exception as exc:
            print("⚠️ Section footprint write failed:", section_id, exc)

        if _is_effectively_empty(new_data):
            return

        try:
            await notify_immediate_access_on_section_update(owner_id, section_id)
        except Exception as exc:
            print("⚠️ Section update notification dispatch failed:", section_id, exc)

    @staticmethod
    async def list_footprints(
        owner_id: str,
        *,
        limit: int = 50,
        section_id: str | None = None,
    ):
        query: dict = {"owner_id": owner_id}
        if section_id:
            query["section_id"] = str(section_id)
        cursor = (
            section_footprints_collection.find(query)
            .sort("created_at", -1)
            .limit(max(1, min(int(limit or 50), 200)))
        )
        rows = []
        async for doc in cursor:
            actor = doc.get("actor") or {}
            created = doc.get("created_at") or datetime.utcnow()
            rows.append(
                {
                    "id": str(doc["_id"]),
                    "section_id": doc.get("section_id"),
                    "section_key": doc.get("section_key"),
                    "source": doc.get("source") or "manual",
                    "scopes": doc.get("scopes") or [],
                    "created_at": (
                        created.isoformat()
                        if hasattr(created, "isoformat")
                        else str(created)
                    ),
                    "actor": {
                        "user_id": actor.get("user_id") or "",
                        "full_name": actor.get("full_name") or "Unknown",
                        "email": actor.get("email") or "",
                        "role": actor.get("role") or "owner",
                        "portal_role": actor.get("portal_role"),
                        "portal_role_label": actor.get("portal_role_label")
                        or "Owner",
                    },
                }
            )
        return rows

    @staticmethod
    async def latest_by_section(owner_id: str) -> list[dict]:
        """Latest last_updated_by stamp per section document."""
        cursor = section_data_collection.find(
            {"owner_id": owner_id, "last_updated_by": {"$exists": True}},
            {
                "section_id": 1,
                "section_key": 1,
                "updated_at": 1,
                "last_updated_by": 1,
            },
        )
        rows = []
        async for doc in cursor:
            actor = doc.get("last_updated_by") or {}
            at = actor.get("at") or doc.get("updated_at")
            rows.append(
                {
                    "section_id": doc.get("section_id"),
                    "section_key": doc.get("section_key"),
                    "updated_at": (
                        at.isoformat() if hasattr(at, "isoformat") else at
                    ),
                    "actor": {
                        "user_id": actor.get("user_id") or "",
                        "full_name": actor.get("full_name") or "Unknown",
                        "email": actor.get("email") or "",
                        "role": actor.get("role") or "owner",
                        "portal_role": actor.get("portal_role"),
                        "portal_role_label": actor.get("portal_role_label")
                        or "Owner",
                        "source": actor.get("source") or "manual",
                    },
                }
            )
        rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
        return rows

    @staticmethod
    async def latest_by_subsection(owner_id: str) -> list[dict]:
        """Latest stamp per subsection / item / nested field scope."""
        cursor = section_data_collection.find(
            {"owner_id": owner_id, "subsection_updates": {"$exists": True}},
            {"section_id": 1, "section_key": 1, "subsection_updates": 1},
        )
        rows: list[dict] = []
        async for doc in cursor:
            updates = doc.get("subsection_updates") or {}
            if not isinstance(updates, dict):
                continue
            for scope, payload in updates.items():
                if not isinstance(payload, dict):
                    continue
                actor = payload.get("actor") or {}
                at = payload.get("updated_at") or actor.get("at")
                rows.append(
                    {
                        "section_id": doc.get("section_id"),
                        "section_key": doc.get("section_key"),
                        "scope_id": str(scope),
                        "subsection_id": str(scope).split(":")[0].split(".")[0],
                        "updated_at": (
                            at.isoformat() if hasattr(at, "isoformat") else at
                        ),
                        "actor": {
                            "user_id": actor.get("user_id") or "",
                            "full_name": actor.get("full_name") or "Unknown",
                            "email": actor.get("email") or "",
                            "role": actor.get("role") or "owner",
                            "portal_role": actor.get("portal_role"),
                            "portal_role_label": actor.get("portal_role_label")
                            or "Owner",
                            "source": actor.get("source")
                            or payload.get("source")
                            or "manual",
                        },
                    }
                )
        rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
        return rows

    @staticmethod
    async def delete(owner_id: str, section_id: str):
        await section_data_collection.delete_one({
            "owner_id": owner_id,
            "section_id": section_id
        })
