import cloudinary
import cloudinary.uploader
from app.config import settings

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True,
)

def upload_file(file, folder: str):
    return cloudinary.uploader.upload(
        file,
        folder=folder,
        resource_type="auto",
        virus_scan="true",        # 🔐 ENABLE SCAN
        invalidate=True,
    )


def delete_file(public_id: str):
    cloudinary.uploader.destroy(public_id, resource_type="auto")
