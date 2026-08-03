from datetime import datetime, timezone

from fastapi import APIRouter, UploadFile, File, Header, HTTPException, Request
from bson import ObjectId

from app.security.token_resolver import decode_access_token
from app.security.section_file_cleanup import delete_owned_file
from app.security.cloudinary_service import upload_file
from cloudinary.exceptions import Error as CloudinaryError
from app.security.file_validation import validate_upload
from app.database import users_collection
from app.auth.portal_roles import can_upload_documents
from app.auth.access_types import is_family_collaborator, resolve_access_type

router = APIRouter(prefix="/uploads", tags=["Uploads"])


async def _actor_stamp(decoded: dict) -> dict:
    role = decoded.get("role")
    if role == "owner":
        user = await users_collection.find_one(
            {"email": decoded["sub"], "role": "owner"}
        )
        return {
            "uploaded_by_name": (user or {}).get("full_name")
            or decoded.get("sub")
            or "Owner",
            "uploaded_by_email": (user or {}).get("email") or decoded.get("sub"),
            "uploaded_by_role": "owner",
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }

    user = None
    try:
        user = await users_collection.find_one(
            {"_id": ObjectId(str(decoded["sub"])), "role": "nextkin"}
        )
    except Exception:
        user = await users_collection.find_one(
            {"email": decoded.get("sub"), "role": "nextkin"}
        )
    if not user:
        raise HTTPException(status_code=401, detail="Collaborator not found")
    if not can_upload_documents(user):
        raise HTTPException(
            status_code=403,
            detail="Your role cannot upload documents. Ask the owner for Editor or higher.",
        )
    access = resolve_access_type(user)
    return {
        "uploaded_by_name": user.get("full_name") or user.get("email") or "Collaborator",
        "uploaded_by_email": user.get("email"),
        "uploaded_by_role": "family" if access == "family" else "nextkin",
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("")
async def upload_asset(
    request: Request,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)
    stamp = await _actor_stamp(decoded)

    try:
        validate_upload(file)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    folder_key = decoded["sub"]
    if decoded.get("role") == "nextkin":
        # Store under owner folder when possible
        try:
            user = await users_collection.find_one(
                {"_id": ObjectId(str(decoded["sub"])), "role": "nextkin"}
            )
            if user and user.get("owner_id"):
                owner = await users_collection.find_one(
                    {"_id": ObjectId(str(user["owner_id"])), "role": "owner"}
                )
                if owner:
                    folder_key = owner.get("email") or folder_key
        except Exception:
            pass

    try:
        result = upload_file(
            file.file,
            folder=f"orderly_affairs/{folder_key}",
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
        **stamp,
    }


@router.post("/delete")
async def delete_upload(
    data: dict,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_access_token(request, authorization)

    if decoded["role"] == "owner":
        public_id = data.get("public_id")
        if not public_id:
            raise HTTPException(status_code=400)
        owner_prefix = f"orderly_affairs/{decoded['sub']}/"
        if not str(public_id).startswith(owner_prefix):
            raise HTTPException(status_code=403, detail="Not authorized to delete this file")
        delete_owned_file(public_id, decoded["sub"])
        return {"status": "deleted"}

    # Family editor+ may delete files they can access under the owner kit
    if decoded["role"] != "nextkin":
        raise HTTPException(status_code=403)

    user = await users_collection.find_one(
        {"_id": ObjectId(str(decoded["sub"])), "role": "nextkin"}
    )
    if not user or not is_family_collaborator(user) or not can_upload_documents(user):
        raise HTTPException(status_code=403)

    public_id = data.get("public_id")
    if not public_id:
        raise HTTPException(status_code=400)

    owner = await users_collection.find_one(
        {"_id": ObjectId(str(user["owner_id"])), "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404)
    owner_prefix = f"orderly_affairs/{owner['email']}/"
    if not str(public_id).startswith(owner_prefix):
        raise HTTPException(status_code=403, detail="Not authorized to delete this file")

    delete_owned_file(public_id, owner["email"])
    return {"status": "deleted"}
