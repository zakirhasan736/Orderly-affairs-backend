from datetime import datetime, timezone
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import feedback_collection, users_collection
from app.security.token_resolver import decode_access_token

feedback_router = APIRouter(prefix="/feedback", tags=["feedback"])
admin_feedback_router = APIRouter(prefix="/admin/feedback", tags=["admin-feedback"])

FeedbackCategory = Literal["idea", "bug", "confusing", "other"]


class FeedbackAttachmentIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    public_id: Optional[str] = Field(default=None, max_length=500)
    name: Optional[str] = Field(default=None, max_length=300)
    type: Optional[str] = Field(default=None, max_length=100)


class FeedbackCreateIn(BaseModel):
    category: FeedbackCategory = "idea"
    message: str = Field(min_length=1, max_length=4000)
    subject: Optional[str] = Field(default=None, max_length=200)
    page_path: Optional[str] = Field(default=None, max_length=500)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    attachments: list[FeedbackAttachmentIn] = Field(default_factory=list, max_length=5)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _owner_id(user: dict) -> str:
    return str(user.get("_id") or user.get("id") or "")


def _admin_email_set() -> set[str]:
    raw = (settings.ADMIN_EMAILS or "").strip()
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


async def require_admin(request: Request, authorization: str | None):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") == "admin":
        return decoded

    if decoded.get("role") == "owner":
        email = str(decoded.get("sub") or "").strip().lower()
        if email and email in _admin_email_set():
            return {**decoded, "role": "admin", "email": email}

        user = await users_collection.find_one({"email": email, "role": "owner"})
        if user and (user.get("is_admin") is True or user.get("role_admin") is True):
            return {
                **decoded,
                "role": "admin",
                "email": email,
                "sub": email,
            }

    raise HTTPException(403, "Admin only")


def _serialize_feedback(doc: dict) -> dict:
    attachments = []
    for item in doc.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        attachments.append(
            {
                "url": url,
                "public_id": item.get("public_id") or None,
                "name": item.get("name") or None,
                "type": item.get("type") or None,
            }
        )

    created = doc.get("created_at") or _utc_now()
    return {
        "id": str(doc["_id"]),
        "owner_id": str(doc.get("owner_id") or ""),
        "owner_email": doc.get("owner_email") or "",
        "category": doc.get("category") or "other",
        "subject": doc.get("subject") or "",
        "message": doc.get("message") or "",
        "page_path": doc.get("page_path") or "",
        "rating": doc.get("rating"),
        "attachments": attachments,
        "status": doc.get("status") or "open",
        "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
    }


@feedback_router.post("/submit")
async def submit_feedback(body: FeedbackCreateIn, user=Depends(get_current_user)):
    if user.get("role") != "owner":
        raise HTTPException(403, "Owners only")

    owner_id = _owner_id(user)
    if not owner_id:
        raise HTTPException(400, "Invalid owner")

    message = body.message.strip()
    if not message:
        raise HTTPException(400, "Message required")

    subject = (body.subject or "").strip() or None
    page_path = (body.page_path or "").strip() or None

    attachments = []
    for item in body.attachments[:5]:
        url = (item.url or "").strip()
        if not url:
            continue
        # Reject obvious non-http payloads
        if not (url.startswith("http://") or url.startswith("https://")):
            raise HTTPException(400, "Attachment URL must be http(s)")
        attachments.append(
            {
                "url": url[:2000],
                "public_id": (item.public_id or None),
                "name": (item.name or None),
                "type": (item.type or None),
            }
        )

    now = _utc_now()
    doc = {
        "owner_id": owner_id,
        "owner_email": user.get("email") or "",
        "category": body.category,
        "subject": subject,
        "message": message,
        "page_path": page_path,
        "rating": body.rating,
        "attachments": attachments,
        "status": "open",
        "created_at": now,
        "updated_at": now,
    }
    result = await feedback_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    return {"feedback": _serialize_feedback(doc)}


@admin_feedback_router.get("/list")
async def admin_list_feedback(
    request: Request,
    authorization: str | None = Header(default=None),
    status: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    await require_admin(request, authorization)

    query: dict = {}
    if status:
        query["status"] = status.strip().lower()
    if category:
        query["category"] = category.strip().lower()

    cursor = (
        feedback_collection.find(query).sort("created_at", -1).limit(limit)
    )
    items = [_serialize_feedback(doc) async for doc in cursor]
    return {"feedback": items, "count": len(items)}


@admin_feedback_router.patch("/{feedback_id}")
async def admin_update_feedback_status(
    feedback_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    status: Literal["open", "reviewed", "closed"] = Query(...),
):
    await require_admin(request, authorization)

    try:
        oid = ObjectId(feedback_id)
    except Exception:
        raise HTTPException(400, "Invalid feedback id")

    updated = await feedback_collection.update_one(
        {"_id": oid},
        {"$set": {"status": status, "updated_at": _utc_now()}},
    )
    if updated.matched_count == 0:
        raise HTTPException(404, "Feedback not found")

    result = await feedback_collection.find_one({"_id": oid})
    if not result:
        raise HTTPException(404, "Feedback not found")
    return {"feedback": _serialize_feedback(result)}
