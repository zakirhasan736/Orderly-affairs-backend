import cloudinary
import cloudinary.uploader
from app.config import settings

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True,
)

CLOUDINARY_RESOURCE_TYPES = ("video", "raw", "image")


def upload_file(file, folder: str):
    return cloudinary.uploader.upload(
        file,
        folder=folder,
        resource_type="auto",
        virus_scan="true",
        invalidate=True,
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

    for candidate in types_to_try:
        try:
            result = cloudinary.uploader.destroy(
                public_id,
                resource_type=candidate,
                invalidate=True,
            )
            status = result.get("result")
            if status in {"ok", "not found"}:
                print(f"✅ Cloudinary deleted {public_id} ({candidate}): {status}")
                return True
        except Exception as exc:
            print(
                f"⚠️ Cloudinary delete attempt failed for {public_id} "
                f"({candidate}): {exc}",
            )
            continue

    print(f"❌ Cloudinary could not delete {public_id}")
    return False
