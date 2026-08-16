from datetime import datetime, timezone

from app.security.token_resolver import decode_owner_or_nok_token
from fastapi import APIRouter, UploadFile, File, Header, HTTPException, Request
from bson import ObjectId

from app.config import settings
from app.security.section_file_cleanup import delete_owned_file, owner_upload_prefix
from app.security.cloudinary_service import signed_delivery_url
from app.security.file_validation import validate_upload
from app.security.document_guard import DocumentGuardError, guard_upload
from app.security.malware_scan import MalwareScanError
from app.database import users_collection
from app.auth.portal_roles import can_upload_documents
from app.auth.access_types import is_family_collaborator, resolve_access_type
from app.storage.section_s3 import (
    is_section_s3_key,
    presign_section_get_url,
    section_s3_owner_prefix,
    upload_section_bytes_to_s3,
)
from app.storage.vault import vault_quota_check

router = APIRouter(prefix="/uploads", tags=["Uploads"])

SIGNED_URL_TTL_SECONDS = 15 * 60  # 15 minutes


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


async def _resolve_owner_email(decoded: dict) -> str:
    if decoded.get("role") == "owner":
        return str(decoded["sub"]).strip().lower()

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
    return str(owner.get("email") or "").strip().lower()


async def _load_owner_user(owner_email: str) -> dict:
    user = await users_collection.find_one({"email": owner_email, "role": "owner"})
    if not user:
        user = await users_collection.find_one({"email": owner_email})
    if not user:
        raise HTTPException(status_code=404, detail="Owner account not found")
    return user


def _legacy_cloudinary_signed_url(public_id: str, resource_type: str | None = None) -> str:
    import time

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
    decoded = decode_owner_or_nok_token(request, authorization)
    stamp = await _actor_stamp(decoded)
    owner_email = await _resolve_owner_email(decoded)

    try:
        validate_upload(file)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        guarded = guard_upload(
            contents,
            mime_type=file.content_type,
            filename=file.filename,
        )
    except (MalwareScanError, DocumentGuardError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    contents = guarded.payload
    mime_type = guarded.mime_type or file.content_type or "application/octet-stream"
    scan = guarded.scan

    if not settings.section_s3_active:
        raise HTTPException(
            status_code=503,
            detail=(
                "File storage is not configured. Set AWS_BUCKET / SECTION_S3_* "
                "and restart the API."
            ),
        )

    owner = await _load_owner_user(owner_email)
    await vault_quota_check(
        user=owner,
        user_id=str(owner.get("_id")),
        incoming_bytes=len(contents),
        owner_email=owner_email,
    )

    try:
        uploaded = upload_section_bytes_to_s3(
            contents=contents,
            owner_email=owner_email,
            mime_type=mime_type,
            original_filename=file.filename,
        )
    except Exception as exc:
        print("❌ Section S3 upload failed:", repr(exc))
        raise HTTPException(
            status_code=500,
            detail="Could not store file on S3. Please try again.",
        ) from exc

    return {
        "name": file.filename,
        "url": uploaded.get("url"),
        "public_id": uploaded.get("public_id"),  # S3 key
        "s3_key": uploaded.get("s3_key"),
        "s3_bucket": uploaded.get("s3_bucket"),
        "storage": "s3",
        "type": uploaded.get("type"),
        "format": uploaded.get("format"),
        "size": uploaded.get("size"),
        "mime_type": uploaded.get("mime_type"),
        "access_mode": "private",
        "url_expires_in": SIGNED_URL_TTL_SECONDS,
        "scan_status": scan.status,
        "scan_engine": scan.engine,
        "scan_sanitized": guarded.sanitized,
        **stamp,
    }


@router.post("/signed-url")
async def refresh_signed_upload_url(
    data: dict,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Fresh presigned (S3) or signed (legacy Cloudinary) URL for a vault attachment."""
    decoded = decode_owner_or_nok_token(request, authorization)
    owner_email = await _resolve_owner_email(decoded)
    public_id = str(data.get("public_id") or data.get("s3_key") or "").strip()
    if not public_id:
        raise HTTPException(status_code=400, detail="public_id required")

    # --- S3 ---
    if is_section_s3_key(public_id) or str(data.get("storage") or "").lower() == "s3":
        allowed = section_s3_owner_prefix(owner_email)
        if not public_id.startswith(allowed):
            raise HTTPException(status_code=403, detail="Not authorized for this file")
        url = presign_section_get_url(
            s3_key=public_id,
            bucket=str(data.get("s3_bucket") or "").strip() or None,
            expires_in=SIGNED_URL_TTL_SECONDS,
        )
        if not url:
            raise HTTPException(status_code=400, detail="Could not sign delivery URL")
        return {
            "public_id": public_id,
            "s3_key": public_id,
            "url": url,
            "storage": "s3",
            "access_mode": "private",
            "url_expires_in": SIGNED_URL_TTL_SECONDS,
        }

    # --- Legacy Cloudinary ---
    prefix = owner_upload_prefix(owner_email)
    if not (public_id == prefix.rstrip("/") or public_id.startswith(prefix)):
        raise HTTPException(status_code=403, detail="Not authorized for this file")

    resource_type = data.get("resource_type") or data.get("type")
    try:
        url = _legacy_cloudinary_signed_url(public_id, resource_type)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not sign delivery URL: {exc}"
        ) from exc

    return {
        "public_id": public_id,
        "url": url,
        "storage": "cloudinary",
        "access_mode": "authenticated",
        "url_expires_in": SIGNED_URL_TTL_SECONDS,
    }


@router.post("/delete")
async def delete_upload(
    data: dict,
    request: Request,
    authorization: str | None = Header(default=None),
):
    decoded = decode_owner_or_nok_token(request, authorization)
    public_id = str(data.get("public_id") or data.get("s3_key") or "").strip()
    if not public_id:
        raise HTTPException(status_code=400, detail="public_id required")

    if decoded["role"] == "owner":
        owner_email = str(decoded["sub"]).strip().lower()
        delete_owned_file(public_id, owner_email)
        return {"status": "deleted", "storage": "s3" if is_section_s3_key(public_id) else "cloudinary"}

    if decoded["role"] != "nextkin":
        raise HTTPException(status_code=403)

    user = await users_collection.find_one(
        {"_id": ObjectId(str(decoded["sub"])), "role": "nextkin"}
    )
    if not user or not is_family_collaborator(user) or not can_upload_documents(user):
        raise HTTPException(status_code=403)

    owner = await users_collection.find_one(
        {"_id": ObjectId(str(user["owner_id"])), "role": "owner"}
    )
    if not owner:
        raise HTTPException(status_code=404)

    delete_owned_file(public_id, owner["email"])
    return {
        "status": "deleted",
        "storage": "s3" if is_section_s3_key(public_id) else "cloudinary",
    }
