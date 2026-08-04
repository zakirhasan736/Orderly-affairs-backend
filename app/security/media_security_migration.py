"""Re-upload legacy public message / letter media as authenticated Cloudinary assets."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

import requests

from app.database import letters_collection, messageofnextkin_collection
from app.security.cloudinary_service import (
    MESSAGE_MEDIA_FOLDER,
    delete_file,
    fetch_authenticated_bytes,
    signed_media_delivery_url,
    upload_file,
    upload_media_file,
)


def _needs_privatize(media: dict | None) -> bool:
    if not isinstance(media, dict):
        return False
    public_id = str(media.get("public_id") or "").strip()
    if not public_id:
        return False
    if str(media.get("access_mode") or "").lower() == "authenticated":
        return False
    return True


def _download_public_or_signed(
    *,
    public_id: str,
    resource_type: str | None,
    secure_url: str | None,
) -> bytes:
    if secure_url:
        try:
            response = requests.get(str(secure_url), timeout=90)
            if response.ok and response.content:
                return response.content
        except Exception:
            pass

    try:
        return fetch_authenticated_bytes(
            public_id,
            resource_type=resource_type,
            timeout=90,
        )
    except Exception:
        pass

    # Last resort: Cloudinary public delivery URL pattern.
    from app.config import settings

    cloud = settings.CLOUDINARY_CLOUD_NAME
    rt = (resource_type or "image").lower()
    if rt == "audio":
        rt = "video"
    url = f"https://res.cloudinary.com/{cloud}/{rt}/upload/{public_id}"
    response = requests.get(url, timeout=90)
    response.raise_for_status()
    return response.content


def _is_av_media(media: dict) -> bool:
    mime = str(media.get("mime_type") or media.get("content_type") or "").lower()
    mtype = str(media.get("type") or media.get("resource_type") or "").lower()
    if mime.startswith(("video/", "audio/")):
        return True
    if mtype in ("video", "audio"):
        return True
    return False


def _folder_for_public_id(public_id: str) -> str:
    if public_id.startswith("letters/media"):
        return "letters/media"
    return MESSAGE_MEDIA_FOLDER


def _reupload_bytes(media: dict, contents: bytes) -> dict:
    public_id = str(media.get("public_id") or "")
    folder = _folder_for_public_id(public_id)
    filename = str(
        media.get("original_filename")
        or media.get("filename")
        or f"{public_id.split('/')[-1]}.bin"
    )
    buffer = BytesIO(contents)
    buffer.name = filename  # type: ignore[attr-defined]

    if _is_av_media(media):
        uploaded = upload_media_file(buffer, folder=folder)
    else:
        uploaded = upload_file(
            buffer,
            folder=folder,
            access_mode="authenticated",
            type="authenticated",
        )
    return uploaded


async def _migrate_collection(
    collection,
    *,
    media_field: str,
    dry_run: bool,
    limit: int | None,
) -> dict[str, Any]:
    scanned = 0
    migrated = 0
    skipped = 0
    failed = 0
    errors: list[str] = []

    cursor = collection.find({f"{media_field}.public_id": {"$exists": True, "$ne": ""}})
    async for doc in cursor:
        if limit is not None and scanned >= limit:
            break
        scanned += 1
        media = doc.get(media_field) or {}
        if not _needs_privatize(media):
            skipped += 1
            continue

        public_id = str(media.get("public_id") or "").strip()
        try:
            contents = _download_public_or_signed(
                public_id=public_id,
                resource_type=media.get("type") or media.get("resource_type"),
                secure_url=media.get("secure_url") or media.get("url"),
            )
        except Exception as exc:
            failed += 1
            errors.append(f"{public_id}: download failed: {exc}")
            continue

        if dry_run:
            migrated += 1
            continue

        try:
            uploaded = _reupload_bytes(media, contents)
            new_pid = str(uploaded.get("public_id") or "").strip()
            if not new_pid:
                raise RuntimeError("authenticated upload returned no public_id")

            resource_type = (
                uploaded.get("resource_type")
                or media.get("type")
                or media.get("resource_type")
                or "image"
            )
            delivery = ""
            try:
                delivery = signed_media_delivery_url(
                    new_pid,
                    resource_type=str(resource_type),
                )
            except Exception:
                delivery = str(uploaded.get("secure_url") or "")

            new_media = {
                **media,
                "public_id": new_pid,
                "secure_url": delivery,
                "url": delivery,
                "type": resource_type,
                "resource_type": resource_type,
                "access_mode": "authenticated",
            }

            await collection.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        media_field: new_media,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )

            try:
                delete_file(public_id, media.get("type") or media.get("resource_type"))
            except Exception:
                pass

            migrated += 1
        except Exception as exc:
            failed += 1
            errors.append(f"{public_id}: re-upload failed: {exc}")

    return {
        "scanned": scanned,
        "migrated": migrated,
        "skipped": skipped,
        "failed": failed,
        "dry_run": dry_run,
        "errors": errors[:25],
    }


async def migrate_message_media_authenticated(
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    return await _migrate_collection(
        messageofnextkin_collection,
        media_field="media",
        dry_run=dry_run,
        limit=limit,
    )


async def migrate_letter_media_authenticated(
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    return await _migrate_collection(
        letters_collection,
        media_field="media",
        dry_run=dry_run,
        limit=limit,
    )


async def run_media_security_migration(
    *,
    dry_run: bool = False,
    messages: bool = True,
    letters: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"dry_run": dry_run}
    if messages:
        result["messages"] = await migrate_message_media_authenticated(
            dry_run=dry_run,
            limit=limit,
        )
    if letters:
        result["letters"] = await migrate_letter_media_authenticated(
            dry_run=dry_run,
            limit=limit,
        )
    return result
