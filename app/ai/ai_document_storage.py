"""
Cloudinary-backed storage helpers for AI autofill documents.

Uploads use authenticated Cloudinary delivery; Mongo holds metadata.
Preview / extract download via short-lived signed URLs — not public links.
"""

from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path
from typing import Optional

import requests

from app.security.cloudinary_service import (
    delete_file,
    fetch_authenticated_bytes,
    signed_delivery_url,
    upload_file,
)
from app.storage.vault import recover_ai_document_path


def ai_cloudinary_folder(email: str | None, user_id: str) -> str:
    owner = (email or user_id or "owner").strip().lower()
    safe = "".join(ch if ch.isalnum() or ch in "._-@" else "_" for ch in owner)
    return f"orderly_affairs/{safe}/ai"


def destroy_ai_document_assets(doc: dict | None) -> None:
    """Delete Cloudinary asset (and any legacy vault path) for one AI document."""
    if not isinstance(doc, dict):
        return

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
    # Prefer a signed delivery URL over a permanent public secure_url.
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
    Download AI document bytes.

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
            # Fall through to legacy URL for pre-migration assets.
            if not secure_url:
                raise auth_exc

    url = str(secure_url or "").strip()
    if not url:
        raise RuntimeError("No Cloudinary public_id or secure_url available")

    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


async def materialize_ai_document_file(doc: dict | None) -> Optional[Path]:
    """
    Return a local filesystem path for extractors / classifiers.

    Prefer vault path when present; otherwise download from Cloudinary
    (authenticated signed URL when possible).
    """
    if not isinstance(doc, dict):
        return None

    path = await recover_ai_document_path(doc)
    if path:
        return path

    public_id = str(doc.get("public_id") or "").strip()
    secure_url = str(doc.get("secure_url") or "").strip()
    if not public_id and not secure_url:
        return None

    mime = str(doc.get("mime_type") or "")
    ext_map = {
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }
    name = str(doc.get("original_filename") or doc.get("stored_filename") or "")
    suffix = Path(name).suffix if "." in name else ext_map.get(mime, ".bin")
    try:
        contents = fetch_cloudinary_bytes(
            public_id=public_id or None,
            resource_type=doc.get("resource_type"),
            secure_url=secure_url or None,
        )
    except Exception as exc:
        print(f"⚠️ Cloudinary download failed: {exc}")
        return None

    return write_temp_ai_file(contents, suffix or ".bin")
