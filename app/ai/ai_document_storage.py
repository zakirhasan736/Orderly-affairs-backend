"""
Storage helpers for AI autofill documents.

Primary: AWS S3 (when VAULT_S3 / AWS_BUCKET configured).
Legacy: Cloudinary authenticated assets + on-disk VAULT_ROOT paths.
"""

from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path
from typing import Optional

import requests

from app.config import settings
from app.security.cloudinary_service import (
    delete_file,
    fetch_authenticated_bytes,
    signed_delivery_url,
    upload_file,
)
from app.storage.vault import recover_ai_document_path
from app.storage.vault_s3 import (
    delete_vault_s3_object,
    fetch_vault_s3_bytes,
    upload_vault_bytes_to_s3,
)


def ai_cloudinary_folder(email: str | None, user_id: str) -> str:
    owner = (email or user_id or "owner").strip().lower()
    safe = "".join(ch if ch.isalnum() or ch in "._-@" else "_" for ch in owner)
    return f"orderly_affairs/{safe}/ai"


def destroy_ai_document_assets(doc: dict | None) -> None:
    """Delete S3 / Cloudinary / legacy vault bytes for one AI document."""
    if not isinstance(doc, dict):
        return

    s3_key = str(doc.get("s3_key") or "").strip()
    if s3_key or str(doc.get("storage") or "").lower() == "s3":
        delete_vault_s3_object(
            s3_key=s3_key or None,
            bucket=str(doc.get("s3_bucket") or "").strip() or None,
        )

    public_id = str(doc.get("public_id") or "").strip()
    if public_id:
        try:
            delete_file(public_id, doc.get("resource_type"))
        except Exception as exc:
            print(f"⚠️ Cloudinary AI delete failed for {public_id}: {exc}")

    path_value = doc.get("path")
    if path_value:
        try:
            path = Path(str(path_value))
            if path.exists() and path.is_file():
                path.unlink()
        except Exception:
            pass


def upload_ai_bytes_to_cloudinary(
    *,
    contents: bytes,
    folder: str,
    filename: str,
    mime_type: str,
) -> dict:
    """Upload PDF / image / txt as an authenticated (non-public) Cloudinary asset."""
    buffer = BytesIO(contents)
    buffer.name = filename  # type: ignore[attr-defined]
    result = upload_file(
        buffer,
        folder=folder,
        access_mode="authenticated",
        type="authenticated",
    )
    public_id = result.get("public_id")
    resource_type = result.get("resource_type") or "auto"
    delivery = ""
    if public_id:
        try:
            delivery = signed_delivery_url(
                str(public_id),
                resource_type=str(resource_type),
            )
        except Exception:
            delivery = str(result.get("secure_url") or "")

    return {
        "public_id": public_id,
        "secure_url": delivery or result.get("secure_url"),
        "resource_type": resource_type,
        "access_mode": "authenticated",
        "format": result.get("format"),
        "bytes": result.get("bytes") or len(contents),
        "mime_type": mime_type,
        "storage": "cloudinary",
    }


def upload_ai_bytes_to_storage(
    *,
    contents: bytes,
    folder_uuid: str,
    stored_filename: str,
    mime_type: str,
    original_filename: str,
    email: str | None = None,
    user_id: str = "",
) -> dict:
    """
    Store upload bytes on S3 (required). Returns fields for ai_documents Mongo row.
    Legacy Cloudinary rows remain readable via fetch/destroy helpers.
    """
    if not settings.vault_s3_active:
        raise RuntimeError(
            "Vault S3 is not configured. Set AWS_BUCKET / VAULT_S3_* and restart."
        )

    meta = upload_vault_bytes_to_s3(
        contents=contents,
        folder_uuid=folder_uuid,
        stored_filename=stored_filename,
        mime_type=mime_type,
        original_filename=original_filename,
    )
    return {
        "storage": "s3",
        "s3_bucket": meta.get("s3_bucket"),
        "s3_key": meta.get("s3_key"),
        "s3_region": meta.get("s3_region"),
        "folder_uuid": folder_uuid,
        "stored_filename": stored_filename,
    }


def write_temp_ai_file(contents: bytes, suffix: str) -> Path:
    """Write bytes to a NamedTemporaryFile for local extract / OCR."""
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(contents)
        tmp.flush()
        return Path(tmp.name)
    finally:
        tmp.close()


def fetch_cloudinary_bytes(
    *,
    public_id: str | None = None,
    resource_type: str | None = None,
    secure_url: str | None = None,
    timeout: int = 60,
) -> bytes:
    """
    Download AI document bytes from Cloudinary.

    Prefer authenticated signed download by public_id; fall back to legacy
    public secure_url for older uploads.
    """
    pid = str(public_id or "").strip()
    if pid:
        try:
            return fetch_authenticated_bytes(
                pid,
                resource_type=resource_type,
                timeout=timeout,
            )
        except Exception as auth_exc:
            if not secure_url:
                raise auth_exc

    url = str(secure_url or "").strip()
    if not url:
        raise RuntimeError("No Cloudinary public_id or secure_url available")

    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


def fetch_ai_document_bytes(doc: dict) -> bytes:
    """Load raw bytes from S3, Cloudinary, or raise if remote store missing."""
    storage = str(doc.get("storage") or "").strip().lower()
    s3_key = str(doc.get("s3_key") or "").strip()
    if storage == "s3" or s3_key:
        return fetch_vault_s3_bytes(
            s3_key=s3_key,
            bucket=str(doc.get("s3_bucket") or "").strip() or None,
        )

    public_id = str(doc.get("public_id") or "").strip()
    secure_url = str(doc.get("secure_url") or "").strip()
    if public_id or secure_url:
        return fetch_cloudinary_bytes(
            public_id=public_id or None,
            resource_type=doc.get("resource_type"),
            secure_url=secure_url or None,
        )

    raise RuntimeError("No remote document bytes available")


def _suffix_for_doc(doc: dict) -> str:
    mime = str(doc.get("mime_type") or "")
    ext_map = {
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }
    name = str(doc.get("original_filename") or doc.get("stored_filename") or "")
    if "." in name:
        return Path(name).suffix
    return ext_map.get(mime, ".bin")


async def materialize_ai_document_file(doc: dict | None) -> Optional[Path]:
    """
    Return a local filesystem path for extractors / classifiers.

    Prefer vault path when present; otherwise download from S3 or Cloudinary.
    """
    if not isinstance(doc, dict):
        return None

    path = await recover_ai_document_path(doc)
    if path:
        return path

    storage = str(doc.get("storage") or "").strip().lower()
    s3_key = str(doc.get("s3_key") or "").strip()
    public_id = str(doc.get("public_id") or "").strip()
    secure_url = str(doc.get("secure_url") or "").strip()

    if not s3_key and storage != "s3" and not public_id and not secure_url:
        return None

    try:
        contents = fetch_ai_document_bytes(doc)
    except Exception as exc:
        print(f"⚠️ Document download failed: {exc}")
        return None

    return write_temp_ai_file(contents, _suffix_for_doc(doc) or ".bin")
