from fastapi import APIRouter, UploadFile, File, Header, HTTPException
from app.security.jwt_handler import verify_token
from app.security.cloudinary_service import upload_file, delete_file
from cloudinary.exceptions import Error as CloudinaryError
from app.security.file_validation import validate_upload 
router = APIRouter(prefix="/uploads", tags=["Uploads"])

@router.post("")
async def upload_asset(
    file: UploadFile = File(...),
    authorization: str = Header(...),
):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    if not decoded:
        raise HTTPException(status_code=401)

    # STEP 1: FILE VALIDATION (size + mime)
    try:
        validate_upload(file)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # STEP 2: CLOUDINARY UPLOAD (virus scan happens here)
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

    # STEP 3: RETURN CLEAN FILE INFO
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
async def delete_upload(data: dict, authorization: str = Header(...)):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)

    if decoded["role"] != "owner":
        raise HTTPException(status_code=403)

    public_id = data.get("public_id")
    if not public_id:
        raise HTTPException(status_code=400)

    delete_file(public_id)
    return {"status": "deleted"}