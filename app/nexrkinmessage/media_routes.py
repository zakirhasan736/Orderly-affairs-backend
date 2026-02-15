from fastapi import APIRouter, UploadFile, File, Header, HTTPException
from app.security.jwt_handler import verify_token
from app.security.cloudinary_service import upload_file, delete_file

router = APIRouter(prefix="/message/media", tags=["Message Media"])

@router.post("")
async def upload_letter_media(
    file: UploadFile = File(...),
    authorization: str = Header(...)
):
    token = authorization.split(" ")[1]
    verify_token(token)

    # ✅ allow only audio/video
    if not file.content_type.startswith(("video/", "audio/")):
        raise HTTPException(400, "Only audio/video allowed")

    # upload to cloudinary
    uploaded = upload_file(
        file.file,
        folder="letters/media"
    )

    return {
        "url": uploaded["secure_url"],
        "public_id": uploaded["public_id"],
        "type": uploaded["resource_type"],
        "format": uploaded.get("format"),
        "size": uploaded.get("bytes"),
    }
