from datetime import datetime, timezone
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import (
    support_messages_collection,
    support_threads_collection,
    users_collection,
)
from app.security.token_resolver import decode_access_token

support_router = APIRouter(prefix="/support", tags=["support-chat"])
admin_support_router = APIRouter(prefix="/admin/support", tags=["admin-support"])


class SupportMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class AdminReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _owner_id(user: dict) -> str:
    return str(user.get("_id") or user.get("id") or "")


def _admin_email_set() -> set[str]:
    raw = (settings.ADMIN_EMAILS or "").strip()
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _serialize_message(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "thread_id": str(doc["thread_id"]),
        "sender": doc.get("sender"),
        "text": doc.get("text") or "",
        "created_at": (doc.get("created_at") or _utc_now()).isoformat(),
    }


def _serialize_thread(doc: dict, *, unread_for: Optional[str] = None) -> dict:
    unread = 0
    if unread_for == "admin":
        unread = int(doc.get("unread_admin") or 0)
    elif unread_for == "owner":
        unread = int(doc.get("unread_owner") or 0)
    return {
        "id": str(doc["_id"]),
        "owner_id": str(doc.get("owner_id") or ""),
        "owner_email": doc.get("owner_email") or "",
        "status": doc.get("status") or "open",
        "subject": doc.get("subject") or "Live support",
        "last_message_at": (doc.get("last_message_at") or _utc_now()).isoformat(),
        "last_preview": doc.get("last_preview") or "",
        "unread": unread,
        "created_at": (doc.get("created_at") or _utc_now()).isoformat(),
    }


async def _get_or_create_owner_thread(user: dict) -> dict:
    owner_id = _owner_id(user)
    if not owner_id:
        raise HTTPException(400, "Invalid owner")

    existing = await support_threads_collection.find_one(
        {"owner_id": owner_id, "status": {"$in": ["open", "pending"]}}
    )
    if existing:
        return existing

    now = _utc_now()
    doc = {
        "owner_id": owner_id,
        "owner_email": user.get("email") or "",
        "status": "open",
        "subject": "Live support",
        "created_at": now,
        "last_message_at": now,
        "last_preview": "",
        "unread_admin": 0,
        "unread_owner": 0,
    }
    result = await support_threads_collection.insert_one(doc)
    doc["_id"] = result.inserted_id

    await support_messages_collection.insert_one(
        {
            "thread_id": result.inserted_id,
            "sender": "system",
            "text": "Connected to live support. An Orderly Affairs agent will reply here shortly.",
            "created_at": now,
        }
    )
    return doc


async def require_admin(request: Request, authorization: str | None):
    """Allow JWT role=admin, or owner accounts flagged/listed as staff."""
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


@support_router.get("/thread")
async def get_my_support_thread(user=Depends(get_current_user)):
    if user.get("role") != "owner":
        raise HTTPException(403, "Owners only")
    thread = await _get_or_create_owner_thread(user)
    return {"thread": _serialize_thread(thread, unread_for="owner")}


@support_router.get("/thread/messages")
async def list_my_support_messages(
    after: Optional[str] = None,
    user=Depends(get_current_user),
):
    if user.get("role") != "owner":
        raise HTTPException(403, "Owners only")
    thread = await _get_or_create_owner_thread(user)
    query: dict = {"thread_id": thread["_id"]}
    if after:
        try:
            query["_id"] = {"$gt": ObjectId(after)}
        except Exception:
            raise HTTPException(400, "Invalid after cursor")

    cursor = support_messages_collection.find(query).sort("_id", 1).limit(200)
    messages = [_serialize_message(doc) async for doc in cursor]

    await support_threads_collection.update_one(
        {"_id": thread["_id"]},
        {"$set": {"unread_owner": 0}},
    )
    return {
        "thread": _serialize_thread({**thread, "unread_owner": 0}, unread_for="owner"),
        "messages": messages,
    }


@support_router.post("/thread/messages")
async def send_owner_support_message(
    body: SupportMessageIn,
    user=Depends(get_current_user),
):
    if user.get("role") != "owner":
        raise HTTPException(403, "Owners only")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Message required")

    thread = await _get_or_create_owner_thread(user)
    now = _utc_now()
    doc = {
        "thread_id": thread["_id"],
        "sender": "owner",
        "text": text,
        "created_at": now,
    }
    result = await support_messages_collection.insert_one(doc)
    doc["_id"] = result.inserted_id

    await support_threads_collection.update_one(
        {"_id": thread["_id"]},
        {
            "$set": {
                "status": "open",
                "last_message_at": now,
                "last_preview": text[:160],
            },
            "$inc": {"unread_admin": 1},
        },
    )
    return {"message": _serialize_message(doc)}


@admin_support_router.get("/threads")
async def admin_list_threads(
    request: Request,
    authorization: str | None = Header(default=None),
    status: Optional[str] = None,
):
    await require_admin(request, authorization)
    query: dict = {}
    if status:
        query["status"] = status
    cursor = (
        support_threads_collection.find(query)
        .sort("last_message_at", -1)
        .limit(100)
    )
    threads = [
        _serialize_thread(doc, unread_for="admin") async for doc in cursor
    ]
    return {"threads": threads}


@admin_support_router.get("/threads/{thread_id}")
async def admin_get_thread(
    thread_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    await require_admin(request, authorization)
    try:
        oid = ObjectId(thread_id)
    except Exception:
        raise HTTPException(400, "Invalid thread id")

    thread = await support_threads_collection.find_one({"_id": oid})
    if not thread:
        raise HTTPException(404, "Thread not found")

    cursor = (
        support_messages_collection.find({"thread_id": oid}).sort("_id", 1).limit(500)
    )
    messages = [_serialize_message(doc) async for doc in cursor]
    await support_threads_collection.update_one(
        {"_id": oid},
        {"$set": {"unread_admin": 0}},
    )
    return {
        "thread": _serialize_thread({**thread, "unread_admin": 0}, unread_for="admin"),
        "messages": messages,
    }


@admin_support_router.post("/threads/{thread_id}/messages")
async def admin_reply_thread(
    thread_id: str,
    body: AdminReplyIn,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Message required")
    try:
        oid = ObjectId(thread_id)
    except Exception:
        raise HTTPException(400, "Invalid thread id")

    thread = await support_threads_collection.find_one({"_id": oid})
    if not thread:
        raise HTTPException(404, "Thread not found")

    now = _utc_now()
    doc = {
        "thread_id": oid,
        "sender": "admin",
        "text": text,
        "created_at": now,
        "admin_sub": admin.get("sub") or admin.get("email") or "admin",
    }
    result = await support_messages_collection.insert_one(doc)
    doc["_id"] = result.inserted_id

    await support_threads_collection.update_one(
        {"_id": oid},
        {
            "$set": {
                "status": "open",
                "last_message_at": now,
                "last_preview": text[:160],
            },
            "$inc": {"unread_owner": 1},
        },
    )
    return {"message": _serialize_message(doc)}


@admin_support_router.post("/threads/{thread_id}/close")
async def admin_close_thread(
    thread_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    await require_admin(request, authorization)
    try:
        oid = ObjectId(thread_id)
    except Exception:
        raise HTTPException(400, "Invalid thread id")

    result = await support_threads_collection.update_one(
        {"_id": oid},
        {"$set": {"status": "closed", "last_message_at": _utc_now()}},
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Thread not found")
    return {"ok": True}
