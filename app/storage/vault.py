"""
Per-owner document vault on VPS disk.

Layout:
  {VAULT_ROOT}/users/{folder_uuid}/{file_id}{ext}

folder_uuid is a random UUID stored on the user document (not the Mongo _id),
so folder paths are not guessable from user ids.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from app.config import settings
from app.database import ai_documents_collection, users_collection


def vault_root() -> Path:
    root = Path(settings.VAULT_ROOT).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    users = root / "users"
    users.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def users_vault_root() -> Path:
    return vault_root() / "users"


async def get_or_create_folder_uuid(user: dict) -> str:
    """Return durable vault folder UUID for this owner; create + persist if missing."""
    existing = user.get("folder_uuid")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()

    user_id = user.get("_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid user")

    folder_uuid = str(uuid.uuid4())
    try:
        oid = user_id if isinstance(user_id, ObjectId) else ObjectId(str(user_id))
    except InvalidId as exc:
        raise HTTPException(status_code=401, detail="Invalid user") from exc

    await users_collection.update_one(
        {"_id": oid, "$or": [{"folder_uuid": {"$exists": False}}, {"folder_uuid": None}]},
        {"$set": {"folder_uuid": folder_uuid}},
    )
    # If another request won the race, read the winner.
    fresh = await users_collection.find_one({"_id": oid}, {"folder_uuid": 1})
    stored = (fresh or {}).get("folder_uuid") or folder_uuid
    user["folder_uuid"] = stored
    return str(stored)


def owner_vault_dir(folder_uuid: str) -> Path:
    safe = str(folder_uuid).strip()
    if not safe or ".." in safe or "/" in safe or "\\" in safe:
        raise HTTPException(status_code=500, detail="Invalid vault folder.")
    root = users_vault_root()
    path = (root / safe).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Invalid vault path.") from exc
    return path


async def ensure_owner_vault_dir(user: dict) -> tuple[str, Path]:
    folder_uuid = await get_or_create_folder_uuid(user)
    directory = owner_vault_dir(folder_uuid)
    directory.mkdir(parents=True, exist_ok=True)
    return folder_uuid, directory


def resolve_vault_file_path(folder_uuid: str, stored_filename: str) -> Path:
    directory = owner_vault_dir(folder_uuid)
    name = Path(stored_filename).name  # strip any path segments
    path = (directory / name).resolve()
    try:
        path.relative_to(directory.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Invalid vault file path.") from exc
    return path


def user_quota_bytes(user: dict | None) -> int:
    if user:
        limits = user.get("enterprise_limits") or {}
        gb = limits.get("storage_gb")
        if gb is not None:
            try:
                return max(0, int(float(gb) * (1024**3)))
            except (TypeError, ValueError):
                pass
    return int(settings.VAULT_USER_QUOTA_BYTES)


async def vault_usage_bytes(*, user_id: str | None = None) -> int:
    match: dict = {"status": {"$ne": "deleted"}}
    if user_id:
        match["user_id"] = user_id
    pipeline = [
        {"$match": match},
        {"$group": {"_id": None, "total": {"$sum": {"$ifNull": ["$size_bytes", 0]}}}},
    ]
    cursor = await ai_documents_collection.aggregate(pipeline).to_list(length=1)
    if not cursor:
        return 0
    return int(cursor[0].get("total") or 0)


async def vault_quota_check(
    *,
    user: dict,
    user_id: str,
    incoming_bytes: int,
) -> None:
    if incoming_bytes <= 0:
        return

    global_used = await vault_usage_bytes()
    global_cap = int(settings.VAULT_GLOBAL_QUOTA_BYTES)
    if global_used + incoming_bytes > global_cap:
        raise HTTPException(
            status_code=507,
            detail=(
                "Document storage is full on this server. "
                "Please try again later or contact support."
            ),
        )

    user_used = await vault_usage_bytes(user_id=user_id)
    user_cap = user_quota_bytes(user)
    if user_used + incoming_bytes > user_cap:
        mb = max(1, user_cap // (1024 * 1024))
        raise HTTPException(
            status_code=413,
            detail=(
                f"Your document storage limit ({mb} MB) would be exceeded. "
                "Delete unused uploads from Overview or a section, then try again."
            ),
        )


async def purge_owner_vault_dir(user: dict | None, *, folder_uuid: str | None = None) -> bool:
    """Remove the owner's vault directory tree. Returns True if a folder was removed."""
    uuid_value = folder_uuid or (user or {}).get("folder_uuid")
    if not uuid_value:
        return False
    try:
        directory = owner_vault_dir(str(uuid_value))
    except HTTPException:
        return False
    if directory.exists() and directory.is_dir():
        shutil.rmtree(directory, ignore_errors=True)
        return True
    return False
