from datetime import datetime, timezone
import time

from fastapi import APIRouter, UploadFile, File, Header, HTTPException, Request
from bson import ObjectId

from app.security.token_resolver import decode_access_token
from app.security.section_file_cleanup import delete_owned_file
from app.security.cloudinary_service import (
    signed_delivery_url,
    upload_file,
)
from cloudinary.exceptions import Error as CloudinaryError
from app.security.file_validation import validate_upload
from app.database import users_collection
from app.auth.portal_roles import can_upload_documents
from app.auth.access_types import is_family_collaborator, resolve_access_type

router = APIRouter(prefix="/uploads", tags=["Uploads"])

# Short-lived signed delivery for vault section attachments.
SIGNED_URL_TTL_SECONDS = 60 * 60  # 1 hour


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


async def _owner_folder_prefix(decoded: dict) -> str:
    """Return the Cloudinary folder prefix this actor may access."""
    if decoded.get("role") == "owner":
        return f"orderly_affairs/{decoded['sub']}/"

    user = None
    try:
        user = await users_collection.find_one(
            {"_id": ObjectId(str(decoded["sub"])), "role": "nextkin"}
        )
    except Exception:
        user = None
    if not user or not user.get("owner_id"):
        raise HTTPException(status_code=403, detail="Not authorized")
    owner = await users_collection.find_one(
        {"_id": ObjectId(str(user["owner_id"])), "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    return f"orderly_affairs/{owner.get('email')}/"


def _signed_url(public_id: str, resource_type: str | None = None) -> str:
    expires_at = int(time.time()) + SIGNED_URL_TTL_SECONDS
    return signed_delivery_url(
        public_id,
        resource_type=resource_type,
        expires_at=expires_at,
    )


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
            access_mode="authenticated",
            type="authenticated",
        )
    except CloudinaryError:
        raise HTTPException(
            status_code=400,
            detail="File failed security scan and was rejected",
        )

    public_id = result.get("public_id")
    resource_type = result.get("resource_type")
    delivery = ""
    if public_id:
        try:
            delivery = _signed_url(str(public_id), resource_type)
        except Exception:
            delivery = str(result.get("secure_url") or "")

    return {
        "name": file.filename,
        "url": delivery or result.get("secure_url"),
        "public_id": public_id,
        "type": resource_type,
        "format": result.get("format"),
        "size": result.get("bytes"),
        "access_mode": "authenticated",
        "url_expires_in": SIGNED_URL_TTL_SECONDS,
        "scan_status": "clean",
        **stamp,
    }


@router.post("/signed-url")
async def refresh_signed_upload_url(
    data: dict,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Return a fresh signed Cloudinary URL for an authenticated vault attachment."""
    decoded = decode_access_token(request, authorization)
    public_id = str(data.get("public_id") or "").strip()
    if not public_id:
        raise HTTPException(status_code=400, detail="public_id required")

    prefix = await _owner_folder_prefix(decoded)
    if not public_id.startswith(prefix.rstrip("/")) and not public_id.startswith(
        prefix
    ):
        # Allow exact folder match without trailing slash quirks
        if not public_id.startswith("orderly_affairs/"):
            raise HTTPException(status_code=403, detail="Not authorized for this file")
        # Owner prefix is email-based; reject if not under allowed owner folder
        allowed = prefix.rstrip("/")
        if not (public_id == allowed or public_id.startswith(allowed + "/")):
            raise HTTPException(status_code=403, detail="Not authorized for this file")

    resource_type = data.get("resource_type") or data.get("type")
    try:
        url = _signed_url(public_id, resource_type)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not sign delivery URL: {exc}"
        ) from exc

    return {
        "public_id": public_id,
        "url": url,
        "access_mode": "authenticated",
        "url_expires_in": SIGNED_URL_TTL_SECONDS,
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
