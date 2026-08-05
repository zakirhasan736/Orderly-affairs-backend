"""Delete section/feedback upload files with owner ownership checks.

Supports:
- S3 keys under orderly-affairs/sections/{owner}/
- Legacy Cloudinary public_ids under orderly_affairs/{owner}/
"""

from typing import Any

from fastapi import HTTPException

from app.security.cloudinary_service import delete_file
from app.storage.section_s3 import (
    delete_section_s3_object,
    is_section_s3_key,
    section_s3_owner_prefix,
)


def owner_upload_prefix(owner_email: str) -> str:
    """Legacy Cloudinary folder prefix."""
    return f"orderly_affairs/{owner_email}/"


def delete_owned_file(public_id: str, owner_email: str) -> None:
    if not public_id:
        return

    key = str(public_id).strip()

    # --- S3 (new) ---
    if is_section_s3_key(key):
        allowed = section_s3_owner_prefix(owner_email)
        if not key.startswith(allowed):
            raise HTTPException(
                status_code=403,
                detail="Not authorized to delete this file",
            )
        delete_section_s3_object(s3_key=key)
        return

    # --- Legacy Cloudinary ---
    prefix = owner_upload_prefix(owner_email)
    if not key.startswith(prefix):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to delete this file",
        )
    delete_file(key)


def process_section_deleted_files(obj: Any, owner_email: str) -> None:
    """Walk nested dict/list payloads and delete _deleted_files with ownership checks."""
    if isinstance(obj, dict):
        for public_id in obj.get("_deleted_files", []) or []:
            delete_owned_file(public_id, owner_email)
        for value in obj.values():
            process_section_deleted_files(value, owner_email)
    elif isinstance(obj, list):
        for item in obj:
            process_section_deleted_files(item, owner_email)
