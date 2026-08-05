"""Owner vault document storage on AWS S3 (AI autofill uploads).

Key layout:
  {VAULT_S3_PREFIX}/{folder_uuid}/{file_id}{ext}

Same bucket as backups by default (AWS_BUCKET), different prefix.
Per-owner quota is enforced in Mongo via size_bytes (default 5 GB).
"""

from __future__ import annotations

from typing import Any

from app.config import settings


def vault_s3_prefix() -> str:
    return (settings.VAULT_S3_PREFIX or "orderly-affairs/vault").strip("/")


def build_vault_s3_key(*, folder_uuid: str, stored_filename: str) -> str:
    safe_folder = str(folder_uuid).strip().replace("\\", "/").strip("/")
    name = str(stored_filename).replace("\\", "/").split("/")[-1]
    if not safe_folder or ".." in safe_folder or not name or ".." in name:
        raise ValueError("Invalid vault S3 key components")
    return f"{vault_s3_prefix()}/{safe_folder}/{name}"


def _s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required for vault S3 storage. Install with: pip install boto3"
        ) from exc

    session_kwargs: dict[str, Any] = {
        "region_name": settings.vault_s3_region_name,
    }
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        session_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        session_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    return boto3.client("s3", **session_kwargs)


def upload_vault_bytes_to_s3(
    *,
    contents: bytes,
    folder_uuid: str,
    stored_filename: str,
    mime_type: str,
    original_filename: str | None = None,
) -> dict[str, Any]:
    """Put object bytes; returns metadata for Mongo ai_documents row."""
    if not settings.vault_s3_active:
        raise RuntimeError("Vault S3 storage is not configured")

    bucket = settings.vault_s3_bucket_name
    if not bucket:
        raise RuntimeError("VAULT_S3_BUCKET or AWS_BUCKET is required")

    key = build_vault_s3_key(
        folder_uuid=folder_uuid,
        stored_filename=stored_filename,
    )
    client = _s3_client()
    extra_args: dict[str, Any] = {
        "ContentType": mime_type or "application/octet-stream",
        "ServerSideEncryption": "AES256",
        "Metadata": {
            "app": "orderly-affairs",
            "kind": "vault-ai-document",
        },
    }
    if original_filename:
        # S3 metadata must be ASCII; keep a sanitized hint only.
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
    return {
        "storage": "s3",
        "s3_bucket": bucket,
        "s3_key": key,
        "s3_region": settings.vault_s3_region_name,
        "bytes": len(contents),
        "mime_type": mime_type,
    }


def fetch_vault_s3_bytes(
    *,
    s3_key: str,
    bucket: str | None = None,
    timeout: int = 60,  # noqa: ARG001 — kept for call-site symmetry
) -> bytes:
    key = str(s3_key or "").strip()
    if not key:
        raise RuntimeError("Missing s3_key")
    bucket_name = (bucket or settings.vault_s3_bucket_name or "").strip()
    if not bucket_name:
        raise RuntimeError("Missing S3 bucket for vault fetch")

    client = _s3_client()
    response = client.get_object(Bucket=bucket_name, Key=key)
    body = response.get("Body")
    if body is None:
        raise RuntimeError("Empty S3 object body")
    return body.read()


def delete_vault_s3_object(
    *,
    s3_key: str | None,
    bucket: str | None = None,
) -> None:
    key = str(s3_key or "").strip()
    if not key:
        return
    bucket_name = (bucket or settings.vault_s3_bucket_name or "").strip()
    if not bucket_name:
        return
    try:
        client = _s3_client()
        client.delete_object(Bucket=bucket_name, Key=key)
    except Exception as exc:
        print(f"⚠️ Vault S3 delete failed for {key}: {exc}")


def purge_owner_vault_s3_prefix(*, folder_uuid: str | None) -> int:
    """Delete all objects under vault/{folder_uuid}/. Returns deleted count."""
    safe = str(folder_uuid or "").strip()
    if not safe or ".." in safe or "/" in safe or "\\" in safe:
        return 0
    if not settings.vault_s3_bucket_name:
        return 0

    bucket = settings.vault_s3_bucket_name
    prefix = f"{vault_s3_prefix()}/{safe}/"
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
            # delete_objects accepts up to 1000 keys
            client.delete_objects(
                Bucket=bucket,
                Delete={
                    "Objects": [{"Key": obj["Key"]} for obj in objects if obj.get("Key")],
                    "Quiet": True,
                },
            )
            deleted += len(objects)
        if not page.get("IsTruncated"):
            break
        continuation = page.get("NextContinuationToken")
    return deleted
