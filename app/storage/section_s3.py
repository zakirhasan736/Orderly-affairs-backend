"""Section vault + feedback attachments on AWS S3.

Key layout (owner-scoped, sync-checkable like Cloudinary folders):
  {SECTION_S3_PREFIX}/{safe_email}/{file_id}{ext}

`public_id` returned to the client is the S3 key so existing
delete / replace / `_deleted_files` flows keep working unchanged.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from app.config import settings


def section_s3_prefix() -> str:
    return (settings.SECTION_S3_PREFIX or "orderly-affairs/sections").strip("/")


def safe_owner_slug(owner_email: str) -> str:
    owner = (owner_email or "owner").strip().lower()
    return "".join(ch if ch.isalnum() or ch in "._-@" else "_" for ch in owner)


def section_s3_owner_prefix(owner_email: str) -> str:
    return f"{section_s3_prefix()}/{safe_owner_slug(owner_email)}/"


def is_section_s3_key(key: str | None) -> bool:
    value = str(key or "").strip()
    if not value:
        return False
    return value.startswith(section_s3_prefix() + "/")


def build_section_s3_key(*, owner_email: str, stored_filename: str) -> str:
    name = str(stored_filename).replace("\\", "/").split("/")[-1]
    if not name or ".." in name:
        raise ValueError("Invalid section S3 filename")
    return f"{section_s3_owner_prefix(owner_email)}{name}"


def _s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required for section S3 storage. Install with: pip install boto3"
        ) from exc

    session_kwargs: dict[str, Any] = {
        "region_name": settings.section_s3_region_name,
    }
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        session_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        session_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    return boto3.client("s3", **session_kwargs)


def _ext_for_file(*, filename: str, mime_type: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix and len(suffix) <= 10:
        return suffix
    mime = (mime_type or "").lower()
    return {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "text/plain": ".txt",
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/webm": ".webm",
    }.get(mime, ".bin")


def upload_section_bytes_to_s3(
    *,
    contents: bytes,
    owner_email: str,
    mime_type: str,
    original_filename: str | None = None,
) -> dict[str, Any]:
    if not settings.section_s3_active:
        raise RuntimeError("Section S3 storage is not configured")

    bucket = settings.section_s3_bucket_name
    if not bucket:
        raise RuntimeError("SECTION_S3_BUCKET or AWS_BUCKET is required")

    file_id = uuid.uuid4().hex
    ext = _ext_for_file(
        filename=original_filename or "",
        mime_type=mime_type,
    )
    stored_filename = f"{file_id}{ext}"
    key = build_section_s3_key(
        owner_email=owner_email,
        stored_filename=stored_filename,
    )

    client = _s3_client()
    extra_args: dict[str, Any] = {
        "ContentType": mime_type or "application/octet-stream",
        "ServerSideEncryption": "AES256",
        "Metadata": {
            "app": "orderly-affairs",
            "kind": "section-upload",
        },
    }
    if original_filename:
        safe_name = "".join(
            ch if ord(ch) < 128 and ch.isprintable() else "_"
            for ch in original_filename
        )[:180]
        if safe_name:
            extra_args["Metadata"]["original_filename"] = safe_name

    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=contents,
        **extra_args,
    )

    url = presign_section_get_url(s3_key=key, bucket=bucket, expires_in=900)
    resource_type = "image"
    mime = (mime_type or "").lower()
    if mime == "application/pdf" or mime.startswith("application/"):
        resource_type = "raw"
    elif mime.startswith("video/"):
        resource_type = "video"
    elif mime.startswith("audio/"):
        resource_type = "video"  # FE treats audio like Cloudinary video type

    return {
        "storage": "s3",
        "s3_bucket": bucket,
        "s3_key": key,
        "s3_region": settings.section_s3_region_name,
        # Keep public_id == s3_key so FE replace/delete stays unchanged.
        "public_id": key,
        "url": url,
        "type": resource_type,
        "format": ext.lstrip(".") or None,
        "size": len(contents),
        "mime_type": mime_type,
        "access_mode": "private",
        "url_expires_in": 900,
    }


def presign_section_get_url(
    *,
    s3_key: str,
    bucket: str | None = None,
    expires_in: int = 900,
) -> str:
    key = str(s3_key or "").strip()
    if not key:
        return ""
    bucket_name = (bucket or settings.section_s3_bucket_name or "").strip()
    if not bucket_name:
        return ""
    client = _s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name, "Key": key},
        ExpiresIn=max(60, int(expires_in)),
    )


def delete_section_s3_object(*, s3_key: str | None, bucket: str | None = None) -> bool:
    key = str(s3_key or "").strip()
    if not key:
        return True
    bucket_name = (bucket or settings.section_s3_bucket_name or "").strip()
    if not bucket_name:
        return False
    try:
        client = _s3_client()
        client.delete_object(Bucket=bucket_name, Key=key)
        return True
    except Exception as exc:
        print(f"⚠️ Section S3 delete failed for {key}: {exc}")
        return False


def purge_owner_section_s3_prefix(*, owner_email: str | None) -> int:
    email = (owner_email or "").strip()
    if not email:
        return 0
    bucket = settings.section_s3_bucket_name
    if not bucket:
        return 0

    prefix = section_s3_owner_prefix(email)
    client = _s3_client()
    deleted = 0
    continuation: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "Bucket": bucket,
            "Prefix": prefix,
            "MaxKeys": 1000,
        }
        if continuation:
            kwargs["ContinuationToken"] = continuation
        page = client.list_objects_v2(**kwargs)
        objects = page.get("Contents") or []
        if objects:
            client.delete_objects(
                Bucket=bucket,
                Delete={
                    "Objects": [
                        {"Key": obj["Key"]} for obj in objects if obj.get("Key")
                    ],
                    "Quiet": True,
                },
            )
            deleted += len(objects)
        if not page.get("IsTruncated"):
            break
        continuation = page.get("NextContinuationToken")
    return deleted
