"""Delete Cloudinary files from section payloads with owner ownership checks."""

from typing import Any

from fastapi import HTTPException

from app.security.cloudinary_service import delete_file


def owner_upload_prefix(owner_email: str) -> str:
    return f"orderly_affairs/{owner_email}/"


def delete_owned_file(public_id: str, owner_email: str) -> None:
    if not public_id:
        return
    prefix = owner_upload_prefix(owner_email)
    if not str(public_id).startswith(prefix):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to delete this file",
        )
    delete_file(public_id)


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
