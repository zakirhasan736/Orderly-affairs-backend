"""Optional AWS S3 upload for versioned daily backups."""

from __future__ import annotations

from pathlib import Path

from app.config import settings


def upload_backup_to_s3(local_path: Path, object_key: str | None = None) -> str | None:
    """
    Upload an encrypted backup file to S3 when backup S3 is active.

    Returns the S3 object key on success, or None when S3 is disabled.
    Enable bucket versioning in AWS so overwrites / same-prefix keys keep history.

    Accepts either BACKUP_S3_BUCKET or AWS_BUCKET (and matching region/keys).
    """
    if not settings.backup_s3_active:
        return None

    bucket = settings.backup_s3_bucket_name
    if not bucket:
        raise RuntimeError(
            "S3 backups require BACKUP_S3_BUCKET or AWS_BUCKET"
        )

    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required for S3 backups. Install with: pip install boto3"
        ) from exc

    prefix = (settings.BACKUP_S3_PREFIX or "orderly-affairs/backups").strip("/")
    key = object_key or f"{prefix}/{local_path.name}"

    session_kwargs: dict = {"region_name": settings.backup_s3_region_name}
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        session_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        session_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY

    client = boto3.client("s3", **session_kwargs)
    extra = {
        "ContentType": "application/octet-stream",
        "Metadata": {
            "app": "orderly-affairs",
            "format": "oa1b",
        },
        # Server-side encryption at rest on S3 (in addition to app-level AES-GCM).
        "ServerSideEncryption": "AES256",
    }

    client.upload_file(
        str(local_path),
        bucket,
        key,
        ExtraArgs=extra,
    )
    return key
