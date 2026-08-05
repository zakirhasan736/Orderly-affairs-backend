"""
Per-owner document vault on VPS disk.

Layout:
  {VAULT_ROOT}/users/{folder_uuid}/{file_id}{ext}

folder_uuid is a random UUID stored on the user document (not the Mongo _id),
so folder paths are not guessable from user ids.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException

from app.config import settings
from app.database import ai_documents_collection, messageofnextkin_collection, users_collection


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


def resolve_stored_ai_document_path(doc: dict | None) -> Path | None:
    """
    Locate an uploaded AI document on disk.

    Prefer vault layout (folder_uuid + stored_filename) so absolute paths stay valid
    even if VAULT_ROOT or the process cwd changed after upload.
    Falls back to legacy absolute path / flat AI_UPLOAD_DIR.
    """
    if not isinstance(doc, dict):
        return None

    candidates: list[Path] = []

    folder_uuid = doc.get("folder_uuid")
    stored = doc.get("stored_filename")
    if folder_uuid and stored:
        try:
            candidates.append(resolve_vault_file_path(str(folder_uuid), str(stored)))
        except HTTPException:
            pass

    raw_path = doc.get("path")
    if raw_path:
        candidates.append(Path(str(raw_path)))

    # Legacy flat folder (pre-vault).
    if stored:
        legacy_root = Path(
            getattr(settings, "AI_UPLOAD_DIR", None)
            or __import__("os").getenv("AI_UPLOAD_DIR", "app/uploads/ai-documents")
        )
        if not legacy_root.is_absolute():
            legacy_root = Path.cwd() / legacy_root
        candidates.append(legacy_root / Path(str(stored)).name)

    # file_id-based name under vault when stored_filename missing.
    file_id = doc.get("_id")
    mime = str(doc.get("mime_type") or "")
    ext_map = {
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }
    if folder_uuid and file_id:
        ext = ext_map.get(mime, "")
        try:
            candidates.append(
                resolve_vault_file_path(str(folder_uuid), f"{file_id}{ext}")
            )
        except HTTPException:
            pass
        # Any extension for this file_id (path/env drift, renamed ext).
        try:
            directory = owner_vault_dir(str(folder_uuid))
            if directory.is_dir():
                for match in directory.glob(f"{file_id}.*"):
                    candidates.append(match)
        except HTTPException:
            pass

    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.exists() and path.is_file():
                return path.resolve()
        except OSError:
            continue
    return None


def _sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


async def recover_ai_document_path(doc: dict | None) -> Path | None:
    """
    Locate bytes for an AI document even when the stored absolute path is from
    another host (e.g. VPS path while running locally) or the row was reused.

    Strategy:
      1) Normal resolve (vault-relative first)
      2) reused_from_file_id sibling
      3) Other same-user docs with the same content_hash
      4) Scan the owner vault for an orphan file with the same content_hash
         and restore it to the expected stored_filename
    """
    if not isinstance(doc, dict):
        return None

    path = resolve_stored_ai_document_path(doc)
    if path:
        return path

    user_id = str(doc.get("user_id") or "")
    file_id = str(doc.get("_id") or "")
    content_hash = str(doc.get("content_hash") or "").strip()
    folder_uuid = doc.get("folder_uuid")
    stored = doc.get("stored_filename")

    reused_id = doc.get("reused_from_file_id")
    if reused_id and str(reused_id) != file_id and user_id:
        prior = await ai_documents_collection.find_one(
            {"_id": reused_id, "user_id": user_id},
        )
        if prior:
            path = resolve_stored_ai_document_path(prior)
            if path:
                return path

    if content_hash and user_id:
        cursor = ai_documents_collection.find(
            {"user_id": user_id, "content_hash": content_hash},
            {"_id": 1, "path": 1, "folder_uuid": 1, "stored_filename": 1, "mime_type": 1},
        )
        async for sibling in cursor:
            if str(sibling.get("_id") or "") == file_id:
                continue
            path = resolve_stored_ai_document_path(sibling)
            if path:
                return path

    # Orphan scan: file left on disk after a Mongo row was replaced/deleted.
    if content_hash and folder_uuid:
        try:
            directory = owner_vault_dir(str(folder_uuid))
        except HTTPException:
            directory = None
        if directory and directory.is_dir():
            for candidate in directory.iterdir():
                if not candidate.is_file():
                    continue
                if file_id and candidate.stem == file_id:
                    return candidate.resolve()
                digest = _sha256_file(candidate)
                if digest != content_hash:
                    continue
                # Restore to the expected vault name so future resolves are cheap.
                if stored:
                    try:
                        target = resolve_vault_file_path(str(folder_uuid), str(stored))
                    except HTTPException:
                        target = candidate
                    if target != candidate and not target.exists():
                        try:
                            shutil.copy2(candidate, target)
                            candidate = target
                        except OSError:
                            pass
                try:
                    await ai_documents_collection.update_one(
                        {"_id": doc.get("_id"), "user_id": user_id},
                        {
                            "$set": {
                                "path": str(candidate.resolve()),
                                "stored_filename": candidate.name,
                            }
                        },
                    )
                except Exception:
                    pass
                return candidate.resolve()

    return None


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


async def message_media_usage_bytes(*, owner_email: str | None = None) -> int:
    """Sum personal-message media sizes for one owner (owner_id is email)."""
    match: dict = {
        "is_deleted": {"$ne": True},
        "media": {"$type": "object"},
    }
    if owner_email:
        match["owner_id"] = owner_email
    pipeline = [
        {"$match": match},
        {
            "$group": {
                "_id": None,
                "total": {"$sum": {"$ifNull": ["$media.size", 0]}},
            }
        },
    ]
    cursor = await messageofnextkin_collection.aggregate(pipeline).to_list(length=1)
    if not cursor:
        return 0
    return int(cursor[0].get("total") or 0)


async def owner_storage_usage_bytes(
    *,
    user_id: str | None = None,
    owner_email: str | None = None,
) -> int:
    """AI vault docs + personal message media for one owner (shared 5 GB)."""
    docs = await vault_usage_bytes(user_id=user_id) if user_id else 0
    media = await message_media_usage_bytes(owner_email=owner_email) if owner_email else 0
    return int(docs) + int(media)


async def vault_quota_check(
    *,
    user: dict,
    user_id: str,
    incoming_bytes: int,
    owner_email: str | None = None,
) -> None:
    if incoming_bytes <= 0:
        return

    email = (owner_email or user.get("email") or "").strip().lower() or None

    global_docs = await vault_usage_bytes()
    global_media = await message_media_usage_bytes()
    global_cap = int(settings.VAULT_GLOBAL_QUOTA_BYTES)
    if global_docs + global_media + incoming_bytes > global_cap:
        raise HTTPException(
            status_code=507,
            detail=(
                "Document storage is full on this server. "
                "Please try again later or contact support."
            ),
        )

    user_used = await owner_storage_usage_bytes(
        user_id=user_id,
        owner_email=email,
    )
    user_cap = user_quota_bytes(user)
    if user_used + incoming_bytes > user_cap:
        if user_cap >= 1024 * 1024 * 1024:
            limit_label = f"{user_cap / (1024 ** 3):.0f} GB"
        else:
            limit_label = f"{max(1, user_cap // (1024 * 1024))} MB"
        raise HTTPException(
            status_code=413,
            detail=(
                f"Your storage limit ({limit_label}) would be exceeded. "
                "This covers autofill documents and personal message media. "
                "Delete unused uploads, then try again."
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
