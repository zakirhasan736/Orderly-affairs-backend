from fastapi import APIRouter, UploadFile, File, Header, HTTPException, Request
from app.security.token_resolver import decode_access_token
from app.security.section_file_cleanup import delete_owned_file
from app.security.cloudinary_service import upload_file
from cloudinary.exceptions import Error as CloudinaryError
from app.security.file_validation import validate_upload
router = APIRouter(prefix="/uploads", tags=["Uploads"])

@router.post("")
async def upload_asset(
    request: Request,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)

    try:
        validate_upload(file)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        result = upload_file(
            file.file,
            folder=f"orderly_affairs/{decoded['sub']}",
        )
    except CloudinaryError:
        raise HTTPException(
            status_code=400,
            detail="File failed security scan and was rejected",
        )

    return {
        "name": file.filename,
        "url": result["secure_url"],
        "public_id": result["public_id"],
        "type": result["resource_type"],
        "format": result.get("format"),
        "size": result.get("bytes"),
        "scan_status": "clean",
    }

@router.post("/delete")
async def delete_upload(
    data: dict,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    public_id = data.get("public_id")
    if not public_id:
        raise HTTPException(status_code=400)

    owner_prefix = f"orderly_affairs/{decoded['sub']}/"
    if not str(public_id).startswith(owner_prefix):
        raise HTTPException(status_code=403, detail="Not authorized to delete this file")

    delete_owned_file(public_id, decoded["sub"])
    return {"status": "deleted"}
