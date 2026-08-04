"""Owner-scoped default vault access areas per family portal role."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.auth.access_types import FAMILY_ACCESS_MONGO_FILTER
from app.auth.family_access import (
    AREA_SPECIFIC_ACCESS,
    FULL_DASHBOARD_ACCESS,
    normalize_family_access_level,
)
from app.auth.portal_roles import PORTAL_ROLES, normalize_portal_role
from app.database import users_collection

OWNER_FIELD = "family_role_area_defaults"


def _empty_role_entry() -> dict[str, Any]:
    return {
        "access_level": FULL_DASHBOARD_ACCESS,
        "authorized_sections": [],
    }


def default_role_matrix() -> dict[str, dict[str, Any]]:
    return {role_id: _empty_role_entry() for role_id in PORTAL_ROLES}


def normalize_role_area_entry(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _empty_role_entry()
    level = normalize_family_access_level(raw.get("access_level"))
    areas = [
        str(s).strip()
        for s in (raw.get("authorized_sections") or [])
        if str(s).strip()
    ]
    if level == FULL_DASHBOARD_ACCESS:
        areas = []
    return {
        "access_level": level,
        "authorized_sections": areas,
    }


def load_role_area_defaults(owner: dict) -> dict[str, dict[str, Any]]:
    stored = owner.get(OWNER_FIELD) or {}
    out = default_role_matrix()
    if isinstance(stored, dict):
        for role_id in PORTAL_ROLES:
            if role_id in stored:
                out[role_id] = normalize_role_area_entry(stored[role_id])
    return out


def persist_payload_for_role(entry: dict[str, Any]) -> dict[str, Any]:
    """Mongo-friendly access fields applied onto family member docs."""
    level = entry["access_level"]
    areas = list(entry.get("authorized_sections") or [])
    return {
        "access_level": (
            "Full Kit Access" if level == FULL_DASHBOARD_ACCESS else "Section-Specific Access"
        ),
        "access_level_label": level,
        "authorized_sections": [] if level == FULL_DASHBOARD_ACCESS else areas,
    }


async def save_role_area_defaults(
    owner: dict,
    roles_payload: dict[str, Any],
    *,
    apply_to_members: bool = True,
    only_roles: list[str] | None = None,
) -> dict[str, Any]:
    current = load_role_area_defaults(owner)
    touched: list[str] = []

    for raw_role, raw_entry in (roles_payload or {}).items():
        role_id = normalize_portal_role(str(raw_role))
        if only_roles and role_id not in only_roles:
            continue
        entry = normalize_role_area_entry(raw_entry)
        if entry["access_level"] == AREA_SPECIFIC_ACCESS and not entry["authorized_sections"]:
            raise ValueError(
                f"{role_id}: mark at least one access area, or choose full dashboard"
            )
        current[role_id] = entry
        touched.append(role_id)

    await users_collection.update_one(
        {"_id": owner["_id"]},
        {"$set": {OWNER_FIELD: current, "updated_at": datetime.utcnow()}},
    )

    updated_members = 0
    if apply_to_members and touched:
        owner_id = str(owner["_id"])
        for role_id in touched:
            persisted = persist_payload_for_role(current[role_id])
            result = await users_collection.update_many(
                {
                    "owner_id": owner_id,
                    "role": "nextkin",
                    "portal_role": role_id,
                    **FAMILY_ACCESS_MONGO_FILTER,
                },
                {
                    "$set": {
                        **persisted,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
            updated_members += int(result.modified_count or 0)

    return {
        "roles": current,
        "updated_roles": touched,
        "members_updated": updated_members,
    }
