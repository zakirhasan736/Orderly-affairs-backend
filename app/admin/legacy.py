"""Legacy access requests — dual-approval workflow (metadata only)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field

from app.admin.audit import log_admin_action
from app.admin.deps import require_admin
from app.database import admin_legacy_collection, users_collection

admin_legacy_router = APIRouter(prefix="/admin/legacy", tags=["admin-legacy"])


class CreateLegacyRequest(BaseModel):
    deceased_email: EmailStr
    deceased_name: Optional[str] = None
    requester_name: str = Field(min_length=1, max_length=120)
    requester_email: EmailStr
    relationship: str = Field(min_length=1, max_length=80)
    designated: bool = False
    death_cert: bool = False
    id_verified: bool = False
    notes: Optional[str] = None


class PatchLegacyRequest(BaseModel):
    death_cert: Optional[bool] = None
    id_verified: Optional[bool] = None
    notes: Optional[str] = None
    status: Optional[
        Literal[
            "under_review",
            "awaiting_2nd",
            "granted",
            "denied",
        ]
    ] = None


class ApproveLegacyRequest(BaseModel):
    note: Optional[str] = None


def _serial(doc: dict) -> dict:
    docs_parts = []
    if doc.get("death_cert"):
        docs_parts.append("Death cert ✓")
    else:
        docs_parts.append("Death cert pending")
    if doc.get("id_verified"):
        docs_parts.append("ID ✓")
    else:
        docs_parts.append("ID pending")
    return {
        "id": str(doc["_id"]),
        "case_id": doc.get("case_id"),
        "deceased_name": doc.get("deceased_name"),
        "deceased_email": doc.get("deceased_email"),
        "requester_name": doc.get("requester_name"),
        "requester_email": doc.get("requester_email"),
        "relationship": doc.get("relationship"),
        "designated": bool(doc.get("designated")),
        "death_cert": bool(doc.get("death_cert")),
        "id_verified": bool(doc.get("id_verified")),
        "documents_label": " · ".join(docs_parts),
        "status": doc.get("status"),
        "approver_a": doc.get("approver_a"),
        "approver_b": doc.get("approver_b"),
        "granted_at": doc.get("granted_at"),
        "notes": doc.get("notes"),
        "nok_claim_emails": doc.get("nok_claim_emails"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


async def _next_case_id() -> str:
    total = await admin_legacy_collection.count_documents({})
    return f"LGC-{total + 7:03d}"


@admin_legacy_router.get("")
async def list_legacy(
    request: Request,
    authorization: str | None = Header(default=None),
    status: str | None = Query(default=None),
):
    await require_admin(request, authorization)
    query: dict = {}
    if status:
        query["status"] = status.lower().strip()
    cursor = admin_legacy_collection.find(query).sort("created_at", -1)
    items = [_serial(doc) async for doc in cursor]
    open_count = await admin_legacy_collection.count_documents(
        {"status": {"$in": ["under_review", "awaiting_2nd"]}}
    )
    return {"items": items, "total": len(items), "open": open_count}


@admin_legacy_router.post("")
async def create_legacy(
    payload: CreateLegacyRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    email = payload.deceased_email.lower().strip()
    owner = await users_collection.find_one({"email": email, "role": "owner"})
    deceased_name = payload.deceased_name or (
        (owner or {}).get("full_name") or email
    )
    now = datetime.utcnow()
    status = "awaiting_2nd" if payload.death_cert and payload.id_verified else "under_review"
    doc = {
        "case_id": await _next_case_id(),
        "deceased_email": email,
        "deceased_name": deceased_name,
        "requester_name": payload.requester_name.strip(),
        "requester_email": payload.requester_email.lower().strip(),
        "relationship": payload.relationship.strip(),
        "designated": payload.designated,
        "death_cert": payload.death_cert,
        "id_verified": payload.id_verified,
        "status": status,
        "approver_a": None,
        "approver_b": None,
        "notes": payload.notes,
        "created_at": now,
        "updated_at": now,
        "created_by": admin.get("email"),
    }
    result = await admin_legacy_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    await log_admin_action(
        admin.get("email") or "",
        "legacy.create",
        doc["case_id"],
        {"deceased": email},
    )
    return _serial(doc)


@admin_legacy_router.patch("/{item_id}")
async def patch_legacy(
    item_id: str,
    payload: PatchLegacyRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    if not ObjectId.is_valid(item_id):
        raise HTTPException(400, "Invalid id")
    doc = await admin_legacy_collection.find_one({"_id": ObjectId(item_id)})
    if not doc:
        raise HTTPException(404, "Legacy request not found")

    updates: dict = {"updated_at": datetime.utcnow()}
    for key in ("death_cert", "id_verified", "notes", "status"):
        val = getattr(payload, key)
        if val is not None:
            updates[key] = val

    # Auto-progress when docs complete
    death = updates.get("death_cert", doc.get("death_cert"))
    ident = updates.get("id_verified", doc.get("id_verified"))
    if death and ident and doc.get("status") == "under_review" and "status" not in updates:
        updates["status"] = "awaiting_2nd"

    await admin_legacy_collection.update_one({"_id": doc["_id"]}, {"$set": updates})
    doc.update(updates)
    await log_admin_action(
        admin.get("email") or "",
        "legacy.update",
        doc.get("case_id"),
        {"status": doc.get("status")},
    )
    return _serial(doc)


@admin_legacy_router.post("/{item_id}/approve")
async def approve_legacy(
    item_id: str,
    payload: ApproveLegacyRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    if not ObjectId.is_valid(item_id):
        raise HTTPException(400, "Invalid id")
    doc = await admin_legacy_collection.find_one({"_id": ObjectId(item_id)})
    if not doc:
        raise HTTPException(404, "Legacy request not found")
    if doc.get("status") in ("granted", "denied"):
        raise HTTPException(400, "Request already closed")
    if not doc.get("death_cert") or not doc.get("id_verified"):
        raise HTTPException(400, "Death certificate and ID must be verified first")

    admin_email = (admin.get("email") or "").lower().strip()
    now = datetime.utcnow()
    updates: dict = {"updated_at": now}

    if not doc.get("approver_a"):
        updates["approver_a"] = {
            "email": admin_email,
            "at": now,
            "note": payload.note,
        }
        updates["status"] = "awaiting_2nd"
    elif not doc.get("approver_b"):
        if (doc.get("approver_a") or {}).get("email") == admin_email:
            raise HTTPException(400, "Second approval must be a different admin")
        updates["approver_b"] = {
            "email": admin_email,
            "at": now,
            "note": payload.note,
        }
        updates["status"] = "granted"
        updates["granted_at"] = now
    else:
        raise HTTPException(400, "Already fully approved")

    await admin_legacy_collection.update_one({"_id": doc["_id"]}, {"$set": updates})
    doc.update(updates)

    granted_nok = 0
    if updates.get("status") == "granted":
        from app.auth.service import admin_release_nok_vault_access

        release = await admin_release_nok_vault_access(
            owner_ref=doc.get("deceased_email") or "",
            admin_email=admin_email,
            note=payload.note,
        )
        granted_nok = int(release.get("upon_death_granted") or 0)
        await admin_legacy_collection.update_one(
            {"_id": doc["_id"]},
            {"$set": {"nok_claim_emails": granted_nok, "updated_at": datetime.utcnow()}},
        )

    await log_admin_action(
        admin_email,
        "legacy.approve",
        doc.get("case_id"),
        {"status": updates.get("status"), "nok_claim_emails": granted_nok},
    )
    serial = _serial(doc)
    serial["nok_claim_emails"] = granted_nok
    return serial


@admin_legacy_router.post("/{item_id}/deny")
async def deny_legacy(
    item_id: str,
    payload: ApproveLegacyRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    if not ObjectId.is_valid(item_id):
        raise HTTPException(400, "Invalid id")
    doc = await admin_legacy_collection.find_one({"_id": ObjectId(item_id)})
    if not doc:
        raise HTTPException(404, "Legacy request not found")

    updates = {
        "status": "denied",
        "updated_at": datetime.utcnow(),
        "denied_by": admin.get("email"),
        "denied_note": payload.note,
    }
    await admin_legacy_collection.update_one({"_id": doc["_id"]}, {"$set": updates})
    doc.update(updates)
    await log_admin_action(
        admin.get("email") or "",
        "legacy.deny",
        doc.get("case_id"),
        {},
    )
    return _serial(doc)
