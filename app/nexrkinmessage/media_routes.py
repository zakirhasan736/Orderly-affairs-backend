from fastapi import APIRouter, UploadFile, File, Header, HTTPException, Request
from app.security.token_resolver import decode_access_token
from app.security.cloudinary_service import upload_file, delete_file

router = APIRouter(prefix="/message/media", tags=["Message Media"])

@router.post("")
async def upload_letter_media(
    request: Request,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)

    if decoded.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only owners can upload message media")

    if not file.content_type or not file.content_type.startswith(("video/", "audio/")):
        raise HTTPException(400, "Only audio/video allowed")

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
