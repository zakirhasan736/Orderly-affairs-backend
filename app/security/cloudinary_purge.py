"""Hard-delete Cloudinary assets for an owner (folders + known public_ids)."""

from __future__ import annotations

from typing import Iterable

import cloudinary.api

from app.security.cloudinary_service import (
    CLOUDINARY_RESOURCE_TYPES,
    MESSAGE_MEDIA_FOLDER,
    delete_file,
)
from app.security.section_file_cleanup import owner_upload_prefix

LETTERS_MEDIA_FOLDER = "letters/media"


def owner_documents_prefix(owner_email: str) -> str:
    """Section vault uploads live under orderly_affairs/{email}/."""
    return owner_upload_prefix(owner_email).rstrip("/")


def _chunked(items: list[str], size: int = 100) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def delete_resources_by_prefix(prefix: str) -> dict:
    """
    Delete every image/video/raw asset under a Cloudinary folder prefix.
    Safe to call when the folder is empty or missing.
    """
    cleaned = (prefix or "").strip().strip("/")
    summary = {
        "prefix": cleaned,
        "deleted": 0,
        "errors": [],
    }
    if not cleaned:
        return summary

    for resource_type in CLOUDINARY_RESOURCE_TYPES:
        next_cursor = None
        try:
            while True:
                kwargs = {
                    "prefix": cleaned,
                    "resource_type": resource_type,
                    "invalidate": True,
                }
                if next_cursor:
                    kwargs["next_cursor"] = next_cursor

                result = cloudinary.api.delete_resources_by_prefix(**kwargs)
                deleted_map = result.get("deleted") or {}
                summary["deleted"] += sum(
                    1
                    for status in deleted_map.values()
                    if status in {"deleted", "not_found"}
                )
                next_cursor = result.get("next_cursor")
                if not next_cursor:
                    break
        except Exception as exc:
            # Empty / missing prefix often raises; keep going for other types.
            summary["errors"].append(f"{resource_type}: {exc}")
            print(
                f"⚠️ Cloudinary prefix delete failed for {cleaned} "
                f"({resource_type}): {exc}",
            )

    # Best-effort remove empty folder node (ignore failures).
    try:
        cloudinary.api.delete_folder(cleaned)
    except Exception:
        pass

    print(
        f"✅ Cloudinary prefix purged {cleaned}: "
        f"{summary['deleted']} resource(s)",
    )
    return summary


def delete_resources_by_public_ids(
    public_ids: Iterable[str],
    *,
    resource_types: Iterable[str] | None = None,
) -> dict:
    """Delete a list of known public_ids (messages/letters shared folders)."""
    ids = sorted({str(pid).strip() for pid in public_ids if pid and str(pid).strip()})
    summary = {"requested": len(ids), "deleted": 0, "errors": []}
    if not ids:
        return summary

    types = list(resource_types or CLOUDINARY_RESOURCE_TYPES)
    for batch in _chunked(ids, 100):
        deleted_any = False
        for resource_type in types:
            try:
                result = cloudinary.api.delete_resources(
                    batch,
                    resource_type=resource_type,
                    invalidate=True,
                )
                deleted_map = result.get("deleted") or {}
                hit = sum(
                    1
                    for status in deleted_map.values()
                    if status in {"deleted", "not_found"}
                )
                if hit:
                    summary["deleted"] += hit
                    deleted_any = True
            except Exception as exc:
                summary["errors"].append(f"{resource_type}: {exc}")

        # Fallback per-id destroy when Admin API batch misses a type.
        if not deleted_any:
            for public_id in batch:
                if delete_file(public_id):
                    summary["deleted"] += 1

    return summary


def purge_owner_cloudinary_media(
    *,
    owner_email: str,
    message_public_ids: Iterable[str] | None = None,
) -> dict:
    """
    Wipe:
    - orderly_affairs/{email}/  (section docs / images / files)
    - messages/media + letters/media assets referenced by this owner
    """
    email = (owner_email or "").strip().lower()
    docs_prefix = owner_documents_prefix(email) if email else ""

    folder_result = (
        delete_resources_by_prefix(docs_prefix) if docs_prefix else {"deleted": 0}
    )

    # Only delete shared-folder assets that belong to this owner (from Mongo).
    shared_ids = [
        pid
        for pid in (message_public_ids or [])
        if str(pid).startswith(f"{MESSAGE_MEDIA_FOLDER}/")
        or str(pid).startswith(f"{LETTERS_MEDIA_FOLDER}/")
    ]
    shared_result = delete_resources_by_public_ids(shared_ids)

    return {
        "owner_folder": docs_prefix,
        "folder": folder_result,
        "shared_media": shared_result,
    }
