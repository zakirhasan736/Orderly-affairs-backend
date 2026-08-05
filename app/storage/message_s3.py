"""Personal-message media (audio / video / photo) on AWS S3.

Key layout (per owner folder_uuid):
  {MESSAGE_S3_PREFIX}/{folder_uuid}/{file_id}{ext}

Same AWS bucket as vault docs & backups by default; separate prefix.
Playback uses short-lived presigned GET URLs.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from app.config import settings


def message_s3_prefix() -> str:
    return (settings.MESSAGE_S3_PREFIX or "orderly-affairs/messages").strip("/")


def build_message_s3_key(*, folder_uuid: str, stored_filename: str) -> str:
    safe_folder = str(folder_uuid).strip().replace("\\", "/").strip("/")
    name = str(stored_filename).replace("\\", "/").split("/")[-1]
    if not safe_folder or ".." in safe_folder or not name or ".." in name:
        raise ValueError("Invalid message S3 key components")
    return f"{message_s3_prefix()}/{safe_folder}/{name}"


def _s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required for message S3 storage. Install with: pip install boto3"
        ) from exc

    session_kwargs: dict[str, Any] = {
        "region_name": settings.message_s3_region_name,
    }
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        session_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        session_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    return boto3.client("s3", **session_kwargs)


def _ext_for_upload(*, filename: str, mime_type: str, kind: str) -> str:
    name = (filename or "").strip()
    suffix = Path(name).suffix.lower() if name else ""
    if suffix and len(suffix) <= 8:
        return suffix
    mime = (mime_type or "").lower()
    if mime.startswith("image/"):
        return {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }.get(mime, ".jpg")
    if mime.startswith("audio/"):
        return {
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "audio/aac": ".aac",
            "audio/wav": ".wav",
            "audio/webm": ".webm",
            "audio/ogg": ".ogg",
        }.get(mime, ".m4a")
    if mime.startswith("video/") or kind == "video":
        return {
            "video/mp4": ".mp4",
            "video/webm": ".webm",
            "video/quicktime": ".mov",
        }.get(mime, ".mp4")
    return ".bin"


def upload_message_bytes_to_s3(
    *,
    contents: bytes,
    folder_uuid: str,
    mime_type: str,
    original_filename: str | None = None,
    kind: str = "video",
) -> dict[str, Any]:
    if not settings.message_s3_active:
        raise RuntimeError("Message S3 storage is not configured")

    bucket = settings.message_s3_bucket_name
    if not bucket:
        raise RuntimeError("MESSAGE_S3_BUCKET or AWS_BUCKET is required")

    file_id = uuid.uuid4().hex
    ext = _ext_for_upload(
        filename=original_filename or "",
        mime_type=mime_type,
        kind=kind,
    )
    stored_filename = f"{file_id}{ext}"
    key = build_message_s3_key(
        folder_uuid=folder_uuid,
        stored_filename=stored_filename,
    )

    client = _s3_client()
    extra_args: dict[str, Any] = {
        "ContentType": mime_type or "application/octet-stream",
        "ServerSideEncryption": "AES256",
        "Metadata": {
            "app": "orderly-affairs",
            "kind": "personal-message-media",
            "media_kind": kind,
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

    url = presign_message_get_url(s3_key=key, bucket=bucket, expires_in=900)
    fmt = ext.lstrip(".") or None
    return {
        "storage": "s3",
        "s3_bucket": bucket,
        "s3_key": key,
        "s3_region": settings.message_s3_region_name,
        "folder_uuid": folder_uuid,
        "stored_filename": stored_filename,
        "url": url,
        "public_id": key,  # stable id for FE replace/delete comparison
        "type": kind,
        "format": fmt,
        "size": len(contents),
        "mime_type": mime_type,
        "access_mode": "private",
        "url_expires_in": 900,
    }


def presign_message_get_url(
    *,
    s3_key: str,
    bucket: str | None = None,
    expires_in: int = 900,
) -> str:
    key = str(s3_key or "").strip()
    if not key:
        return ""
    bucket_name = (bucket or settings.message_s3_bucket_name or "").strip()
    if not bucket_name:
        return ""
    client = _s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name, "Key": key},
        ExpiresIn=max(60, int(expires_in)),
    )


def delete_message_s3_object(
    *,
    s3_key: str | None,
    bucket: str | None = None,
) -> bool:
    key = str(s3_key or "").strip()
    if not key:
        return True
    bucket_name = (bucket or settings.message_s3_bucket_name or "").strip()
    if not bucket_name:
        return False
    try:
        client = _s3_client()
        client.delete_object(Bucket=bucket_name, Key=key)
        return True
    except Exception as exc:
        print(f"⚠️ Message S3 delete failed for {key}: {exc}")
        return False


def key_belongs_to_owner_folder(*, s3_key: str, folder_uuid: str) -> bool:
    key = str(s3_key or "").strip().replace("\\", "/")
    folder = str(folder_uuid or "").strip()
    if not key or not folder:
        return False
    expected = f"{message_s3_prefix()}/{folder}/"
    return key.startswith(expected)


def purge_owner_message_s3_prefix(*, folder_uuid: str | None) -> int:
    safe = str(folder_uuid or "").strip()
    if not safe or ".." in safe or "/" in safe or "\\" in safe:
        return 0
    bucket = settings.message_s3_bucket_name
    if not bucket:
        return 0

    prefix = f"{message_s3_prefix()}/{safe}/"
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
