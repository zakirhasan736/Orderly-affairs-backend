"""DSAR (data subject access request) tracker — metadata only."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field

from app.admin.audit import log_admin_action
from app.admin.deps import require_admin
from app.database import admin_dsar_collection

admin_dsar_router = APIRouter(prefix="/admin/dsar", tags=["admin-dsar"])

DSAR_TYPES = ("export", "delete", "correct", "restrict")
DSAR_STATUSES = (
    "new",
    "awaiting_id",
    "in_progress",
    "completed",
    "rejected",
)


class CreateDsarRequest(BaseModel):
    requester_email: EmailStr
    request_type: Literal["export", "delete", "correct", "restrict"] = "export"
    notes: Optional[str] = None
    owner_email: Optional[EmailStr] = None


class PatchDsarRequest(BaseModel):
    status: Optional[
        Literal["new", "awaiting_id", "in_progress", "completed", "rejected"]
    ] = None
    notes: Optional[str] = None


def _serial(doc: dict) -> dict:
    received = doc.get("received_at") or doc.get("created_at")
    deadline = doc.get("deadline_at")
    days_left = None
    if isinstance(deadline, datetime):
        days_left = max(0, (deadline.date() - datetime.utcnow().date()).days)
    return {
        "id": str(doc["_id"]),
        "case_id": doc.get("case_id"),
        "requester_email": doc.get("requester_email"),
        "owner_email": doc.get("owner_email"),
        "request_type": doc.get("request_type"),
        "status": doc.get("status"),
        "notes": doc.get("notes"),
        "received_at": received,
        "deadline_at": deadline,
        "days_left": days_left,
        "updated_at": doc.get("updated_at"),
        "created_by": doc.get("created_by"),
    }


async def _next_case_id() -> str:
    total = await admin_dsar_collection.count_documents({})
    return f"DSAR-{total + 41:03d}"


@admin_dsar_router.get("")
async def list_dsar(
    request: Request,
    authorization: str | None = Header(default=None),
    status: str | None = Query(default=None),
):
    await require_admin(request, authorization)
    query: dict = {}
    if status:
        query["status"] = status.lower().strip()
    cursor = admin_dsar_collection.find(query).sort("deadline_at", 1)
    items = [_serial(doc) async for doc in cursor]
    open_count = await admin_dsar_collection.count_documents(
        {"status": {"$nin": ["completed", "rejected"]}}
    )
    return {"items": items, "total": len(items), "open": open_count}


@admin_dsar_router.post("")
async def create_dsar(
    payload: CreateDsarRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    now = datetime.utcnow()
    doc = {
        "case_id": await _next_case_id(),
        "requester_email": payload.requester_email.lower().strip(),
        "owner_email": (payload.owner_email or payload.requester_email)
        .lower()
        .strip(),
        "request_type": payload.request_type,
        "status": "new",
        "notes": payload.notes,
        "received_at": now,
        "deadline_at": now + timedelta(days=45),
        "created_at": now,
        "updated_at": now,
        "created_by": admin.get("email"),
    }
    result = await admin_dsar_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    await log_admin_action(
        admin.get("email") or "",
        "dsar.create",
        doc["case_id"],
        {"type": payload.request_type},
    )
    return _serial(doc)


@admin_dsar_router.patch("/{item_id}")
async def patch_dsar(
    item_id: str,
    payload: PatchDsarRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    if not ObjectId.is_valid(item_id):
        raise HTTPException(400, "Invalid id")
    doc = await admin_dsar_collection.find_one({"_id": ObjectId(item_id)})
    if not doc:
        raise HTTPException(404, "DSAR request not found")

    updates: dict = {"updated_at": datetime.utcnow()}
    if payload.status:
        updates["status"] = payload.status
    if payload.notes is not None:
        updates["notes"] = payload.notes

    await admin_dsar_collection.update_one(
        {"_id": doc["_id"]}, {"$set": updates}
    )
    doc.update(updates)
    await log_admin_action(
        admin.get("email") or "",
        "dsar.update",
        doc.get("case_id"),
        {"status": updates.get("status")},
    )
    return _serial(doc)
