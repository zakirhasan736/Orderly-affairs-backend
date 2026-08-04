"""Migrate legacy AI documents: encrypt extracted_text + privatize Cloudinary."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

from app.ai.ai_document_storage import (
    ai_cloudinary_folder,
    fetch_cloudinary_bytes,
    upload_ai_bytes_to_cloudinary,
)
from app.ai.ai_extract_crypto import encrypt_extracted_text, read_extracted_text
from app.database import ai_documents_collection, users_collection
from app.security.cloudinary_service import delete_file
from app.security.crypto import decrypt_data, is_encrypted_payload


def _already_encrypted_extract(doc: dict) -> bool:
    raw = doc.get("extracted_text")
    if raw is None or raw == "":
        return True
    text = str(raw)
    if not is_encrypted_payload(text):
        return False
    user_id = str(doc.get("user_id") or doc.get("owner_id") or "")
    file_id = str(doc.get("_id") or "")
    try:
        from app.ai.ai_extract_crypto import ai_extract_context

        decrypt_data(text, context=ai_extract_context(user_id, file_id))
        return True
    except Exception:
        try:
            decrypt_data(text)
            return True
        except Exception:
            # Looks like base64 but is not our ciphertext — treat as plaintext.
            return False


def _needs_cloudinary_privatize(doc: dict) -> bool:
    public_id = str(doc.get("public_id") or "").strip()
    if not public_id:
        return False
    if str(doc.get("access_mode") or "").lower() == "authenticated":
        return False
    # Explicit flag or missing access_mode on Cloudinary-backed row.
    return True


async def _owner_email(user_id: str) -> str | None:
    if not user_id:
        return None
    try:
        from bson import ObjectId

        user = await users_collection.find_one({"_id": ObjectId(user_id)})
    except Exception:
        user = await users_collection.find_one({"email": user_id})
    if not user:
        return None
    return str(user.get("email") or "") or None


async def migrate_ai_extracted_text(*, dry_run: bool = False) -> dict[str, Any]:
    """Encrypt plaintext ai_documents.extracted_text rows."""
    scanned = 0
    encrypted = 0
    skipped = 0
    failed = 0

    cursor = ai_documents_collection.find(
        {"extracted_text": {"$exists": True, "$nin": [None, ""]}}
    )
    async for doc in cursor:
        scanned += 1
        if _already_encrypted_extract(doc):
            skipped += 1
            continue

        user_id = str(doc.get("user_id") or doc.get("owner_id") or "")
        file_id = str(doc.get("_id") or "")
        plaintext = read_extracted_text(doc)
        if not plaintext:
            skipped += 1
            continue

        cipher = encrypt_extracted_text(
            user_id=user_id,
            file_id=file_id,
            text=plaintext,
        )
        if dry_run:
            encrypted += 1
            continue

        try:
            await ai_documents_collection.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "extracted_text": cipher,
                        "extracted_text_encrypted": True,
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
            encrypted += 1
        except Exception:
            failed += 1

    return {
        "scanned": scanned,
        "encrypted": encrypted,
        "skipped": skipped,
        "failed": failed,
        "dry_run": dry_run,
    }


async def migrate_ai_cloudinary_authenticated(
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """
    Re-upload legacy public Cloudinary AI assets as authenticated media.

    Downloads via existing public_id / secure_url, uploads as authenticated,
    updates Mongo, then destroys the old public asset when public_id changes
    or after a successful authenticated re-upload of the same id.
    """
    scanned = 0
    migrated = 0
    skipped = 0
    failed = 0
    errors: list[str] = []

    query = {
        "public_id": {"$exists": True, "$nin": [None, ""]},
        "$or": [
            {"access_mode": {"$exists": False}},
            {"access_mode": {"$ne": "authenticated"}},
            {"access_mode": None},
            {"access_mode": ""},
        ],
    }
    cursor = ai_documents_collection.find(query)
    async for doc in cursor:
        if limit is not None and scanned >= limit:
            break
        scanned += 1
        if not _needs_cloudinary_privatize(doc):
            skipped += 1
            continue

        public_id = str(doc.get("public_id") or "").strip()
        try:
            contents = fetch_cloudinary_bytes(
                public_id=public_id,
                resource_type=doc.get("resource_type"),
                secure_url=doc.get("secure_url"),
            )
        except Exception as exc:
            failed += 1
            errors.append(f"{public_id}: download failed: {exc}")
            continue

        if dry_run:
            migrated += 1
            continue

        user_id = str(doc.get("user_id") or doc.get("owner_id") or "")
        email = await _owner_email(user_id)
        folder = ai_cloudinary_folder(email, user_id)
        filename = str(
            doc.get("original_filename")
            or doc.get("stored_filename")
            or f"{public_id.split('/')[-1]}.bin"
        )
        mime = str(doc.get("mime_type") or "application/octet-stream")

        try:
            uploaded = upload_ai_bytes_to_cloudinary(
                contents=contents,
                folder=folder,
                filename=filename,
                mime_type=mime,
            )
            new_pid = str(uploaded.get("public_id") or "").strip()
            if not new_pid:
                raise RuntimeError("authenticated upload returned no public_id")

            await ai_documents_collection.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "public_id": new_pid,
                        "secure_url": uploaded.get("secure_url") or "",
                        "resource_type": uploaded.get("resource_type")
                        or doc.get("resource_type"),
                        "access_mode": "authenticated",
                        "storage": "cloudinary",
                        "updated_at": datetime.utcnow(),
                    }
                },
            )

            # Destroy legacy public asset if the id changed (or same id after type change).
            try:
                delete_file(public_id, doc.get("resource_type"))
                if new_pid != public_id:
                    # Also try destroy on old id under image/raw already handled.
                    pass
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


async def run_ai_security_migration(
    *,
    dry_run: bool = False,
    cloudinary: bool = True,
    extracts: bool = True,
    cloudinary_limit: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"dry_run": dry_run}
    if extracts:
        result["extracted_text"] = await migrate_ai_extracted_text(dry_run=dry_run)
    if cloudinary:
        result["cloudinary"] = await migrate_ai_cloudinary_authenticated(
            dry_run=dry_run,
            limit=cloudinary_limit,
        )
    return result
