import time

import cloudinary
import cloudinary.uploader
import cloudinary.utils
from app.config import settings

MESSAGE_MEDIA_MAX_BYTES = 150 * 1024 * 1024  # 150 MB
MESSAGE_MEDIA_FOLDER = "messages/media"

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True,
)

CLOUDINARY_RESOURCE_TYPES = ("video", "raw", "image")


def upload_file(
    file,
    folder: str,
    *,
    access_mode: str | None = "authenticated",
    type: str | None = "authenticated",
):
    """
    Upload a file to Cloudinary.

    Defaults to authenticated delivery so vault / message assets are not
    anonymously downloadable from a leaked URL. Pass access_mode=\"\" / type=\"\"
    only for intentionally public marketing assets.
    """
    kwargs: dict = {
        "folder": folder,
        "resource_type": "auto",
        "virus_scan": "true",
        "invalidate": True,
    }
    if access_mode:
        kwargs["access_mode"] = access_mode
    if type:
        kwargs["type"] = type
    return cloudinary.uploader.upload(file, **kwargs)


def signed_delivery_url(
    public_id: str,
    *,
    resource_type: str | None = None,
    expires_at: int | None = None,
) -> str:
    """
    Build a short-lived signed HTTPS URL for an authenticated Cloudinary asset.
    """
    if not public_id:
        return ""

    rt = _normalize_resource_type(resource_type) or "image"
    # PDFs / txt often land as raw when resource_type=auto.
    if rt == "auto":
        rt = "raw"

    options: dict = {
        "resource_type": rt,
        "type": "authenticated",
        "sign_url": True,
        "secure": True,
    }
    if expires_at:
        options["expires_at"] = int(expires_at)

    url, _ = cloudinary.utils.cloudinary_url(public_id, **options)
    return str(url or "")


def fetch_authenticated_bytes(
    public_id: str,
    *,
    resource_type: str | None = None,
    timeout: int = 60,
) -> bytes:
    """Download bytes via a signed authenticated URL (falls back across types)."""
    import requests

    preferred = _normalize_resource_type(resource_type)
    types_to_try: list[str] = []
    if preferred:
        types_to_try.append(preferred)
    for candidate in CLOUDINARY_RESOURCE_TYPES:
        if candidate not in types_to_try:
            types_to_try.append(candidate)

    last_error: Exception | None = None
    for candidate in types_to_try:
        url = signed_delivery_url(public_id, resource_type=candidate)
        if not url:
            continue
        try:
            response = requests.get(url, timeout=timeout)
            if response.status_code == 200 and response.content:
                return response.content
            last_error = RuntimeError(
                f"Cloudinary HTTP {response.status_code} for {public_id} ({candidate})"
            )
        except Exception as exc:
            last_error = exc
            continue

    if last_error:
        raise last_error
    raise RuntimeError(f"Could not download Cloudinary asset {public_id}")


def validate_message_media_size(size: int) -> None:
    if size > MESSAGE_MEDIA_MAX_BYTES:
        raise ValueError("File too large. Maximum size is 150 MB.")


def generate_message_media_upload_signature(
    resource_type: str = "video",
) -> dict:
    """Return signed params for direct browser uploads to Cloudinary (authenticated)."""
    normalized = resource_type if resource_type in ("video", "image") else "video"
    timestamp = int(time.time())
    params_to_sign = {
        "timestamp": timestamp,
        "folder": MESSAGE_MEDIA_FOLDER,
        "type": "authenticated",
        "access_mode": "authenticated",
    }

    return {
        "signature": cloudinary.utils.api_sign_request(
            params_to_sign,
            settings.CLOUDINARY_API_SECRET,
        ),
        "timestamp": timestamp,
        "api_key": settings.CLOUDINARY_API_KEY,
        "cloud_name": settings.CLOUDINARY_CLOUD_NAME,
        "folder": MESSAGE_MEDIA_FOLDER,
        "resource_type": normalized,
        "type": "authenticated",
        "access_mode": "authenticated",
        "max_bytes": MESSAGE_MEDIA_MAX_BYTES,
    }


def upload_media_file(file, folder: str):
    """Upload audio/video up to 150 MB as authenticated media; large files use chunked upload."""
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)

    validate_message_media_size(size)

    common_kwargs = {
        "folder": folder,
        "resource_type": "video",
        "invalidate": True,
        "type": "authenticated",
        "access_mode": "authenticated",
    }

    if size > 20 * 1024 * 1024:
        return cloudinary.uploader.upload_large(file, chunk_size=6_000_000, **common_kwargs)

    return cloudinary.uploader.upload(file, virus_scan="true", **common_kwargs)


def signed_media_delivery_url(
    public_id: str,
    *,
    resource_type: str | None = None,
    ttl_seconds: int = 3600,
) -> str:
    """Short-lived signed URL for authenticated message / letter media."""
    return signed_delivery_url(
        public_id,
        resource_type=resource_type or "video",
        expires_at=int(time.time()) + max(60, int(ttl_seconds)),
    )


def _normalize_resource_type(resource_type: str | None) -> str | None:
    if not resource_type or resource_type == "auto":
        return None

    lowered = str(resource_type).lower()
    if lowered in CLOUDINARY_RESOURCE_TYPES:
        return lowered

    # Audio uploads are commonly stored under the "video" resource type.
    if lowered == "audio":
        return "video"

    return None


def delete_file(public_id: str, resource_type: str | None = None) -> bool:
    """Hard-delete a Cloudinary asset. Returns True if deleted or already gone."""
    if not public_id:
        return False

    preferred = _normalize_resource_type(resource_type)
    types_to_try: list[str] = []

    if preferred:
        types_to_try.append(preferred)

    for candidate in CLOUDINARY_RESOURCE_TYPES:
        if candidate not in types_to_try:
            types_to_try.append(candidate)

    delivery_types = ("upload", "authenticated", "private")

    for candidate in types_to_try:
        for delivery in delivery_types:
            try:
                result = cloudinary.uploader.destroy(
                    public_id,
                    resource_type=candidate,
                    type=delivery,
                    invalidate=True,
                )
                status = result.get("result")
                if status in {"ok", "not found"}:
                    print(
                        f"✅ Cloudinary deleted {public_id} "
                        f"({candidate}/{delivery}): {status}"
                    )
                    return True
            except Exception as exc:
                print(
                    f"⚠️ Cloudinary delete attempt failed for {public_id} "
                    f"({candidate}/{delivery}): {exc}",
                )
                continue

    print(f"❌ Cloudinary could not delete {public_id}")
    return False
