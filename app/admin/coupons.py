"""Admin platform coupons (trial days or lifetime complimentary)."""

from __future__ import annotations

import secrets
import string
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.admin.audit import log_admin_action
from app.admin.deps import require_admin
from app.admin.permissions import (
    user_can_issue_coupons,
    user_can_issue_lifetime_coupons,
)
from app.database import admin_coupons_collection

admin_coupons_router = APIRouter(prefix="/admin/coupons", tags=["admin-coupons"])


class GenerateCouponsRequest(BaseModel):
    kind: Literal["duration", "lifetime"]
    duration_days: Optional[int] = Field(default=None, ge=1, le=3650)
    quantity: int = Field(default=1, ge=1, le=500)
    expires_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=500)
    plan_label: Optional[str] = Field(default=None, max_length=100)


def _generate_code(length: int = 12) -> str:
    alphabet = string.ascii_uppercase + string.digits
    # Avoid ambiguous chars
    alphabet = alphabet.replace("0", "").replace("O", "").replace("1", "").replace("I", "")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _serialize_coupon(doc: dict) -> dict:
    return {
        "code": doc.get("code"),
        "kind": doc.get("kind"),
        "duration_days": doc.get("duration_days"),
        "status": doc.get("status"),
        "expires_at": doc.get("expires_at"),
        "note": doc.get("note"),
        "plan_label": doc.get("plan_label"),
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "redeemed_at": doc.get("redeemed_at"),
        "redeemed_by": doc.get("redeemed_by"),
        "revoked_at": doc.get("revoked_at"),
    }


async def _unique_code() -> str:
    for _ in range(20):
        code = _generate_code()
        exists = await admin_coupons_collection.find_one({"code": code})
        if not exists:
            return code
    raise HTTPException(500, "Could not generate unique coupon code")


@admin_coupons_router.post("/generate")
async def generate_coupons(
    payload: GenerateCouponsRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    if not user_can_issue_coupons(admin):
        raise HTTPException(403, "Not allowed to issue coupons")
    if payload.kind == "lifetime" and not user_can_issue_lifetime_coupons(admin):
        raise HTTPException(403, "Only super admins may issue lifetime coupons")

    if payload.kind == "duration" and not payload.duration_days:
        raise HTTPException(400, "duration_days required for duration coupons")

    now = datetime.utcnow()
    created = []
    for _ in range(payload.quantity):
        code = await _unique_code()
        doc = {
            "code": code,
            "kind": payload.kind,
            "duration_days": payload.duration_days if payload.kind == "duration" else None,
            "status": "unused",
            "expires_at": payload.expires_at,
            "note": payload.note,
            "plan_label": payload.plan_label,
            "created_at": now,
            "created_by": admin.get("email"),
            "redeemed_at": None,
            "redeemed_by": None,
            "revoked_at": None,
        }
        await admin_coupons_collection.insert_one(doc)
        created.append(_serialize_coupon(doc))

    await log_admin_action(
        admin.get("email") or "",
        "coupons_generate",
        meta={
            "kind": payload.kind,
            "quantity": payload.quantity,
            "duration_days": payload.duration_days,
        },
    )
    return {"coupons": created, "count": len(created)}


@admin_coupons_router.get("/")
async def list_coupons(
    request: Request,
    authorization: str | None = Header(default=None),
    status: Optional[Literal["unused", "redeemed", "expired", "revoked"]] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    await require_admin(request, authorization)
    now = datetime.utcnow()
    query: dict = {}

    if status == "unused":
        query["status"] = "unused"
        query["$or"] = [
            {"expires_at": None},
            {"expires_at": {"$gt": now}},
        ]
    elif status == "expired":
        query["status"] = "unused"
        query["expires_at"] = {"$lte": now}
    elif status == "redeemed":
        query["status"] = "redeemed"
    elif status == "revoked":
        query["status"] = "revoked"

    total = await admin_coupons_collection.count_documents(query)
    skip = (page - 1) * page_size
    cursor = (
        admin_coupons_collection.find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(page_size)
    )
    items = [_serialize_coupon(doc) async for doc in cursor]
    return {"coupons": items, "page": page, "page_size": page_size, "total": total}


@admin_coupons_router.get("/stats")
async def coupon_stats(
    request: Request,
    authorization: str | None = Header(default=None),
):
    await require_admin(request, authorization)
    now = datetime.utcnow()

    unused = await admin_coupons_collection.count_documents(
        {
            "status": "unused",
            "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}],
        }
    )
    expired = await admin_coupons_collection.count_documents(
        {"status": "unused", "expires_at": {"$lte": now}}
    )
    redeemed = await admin_coupons_collection.count_documents({"status": "redeemed"})
    revoked = await admin_coupons_collection.count_documents({"status": "revoked"})
    lifetime = await admin_coupons_collection.count_documents({"kind": "lifetime"})
    duration = await admin_coupons_collection.count_documents({"kind": "duration"})

    return {
        "unused": unused,
        "expired": expired,
        "redeemed": redeemed,
        "revoked": revoked,
        "lifetime": lifetime,
        "duration": duration,
        "total": unused + expired + redeemed + revoked,
    }


@admin_coupons_router.delete("/{code}")
async def revoke_coupon(
    code: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    if not user_can_issue_coupons(admin):
        raise HTTPException(403, "Not allowed to revoke coupons")
    normalized = code.strip().upper()
    doc = await admin_coupons_collection.find_one({"code": normalized})
    if not doc:
        # try case-insensitive
        doc = await admin_coupons_collection.find_one(
            {"code": {"$regex": f"^{normalized}$", "$options": "i"}}
        )
    if not doc:
        raise HTTPException(404, "Coupon not found")
    if doc.get("status") != "unused":
        raise HTTPException(400, "Only unused coupons can be revoked")

    now = datetime.utcnow()
    await admin_coupons_collection.update_one(
        {"_id": doc["_id"]},
        {"$set": {"status": "revoked", "revoked_at": now}},
    )
    await log_admin_action(
        admin.get("email") or "",
        "coupon_revoke",
        target=doc.get("code"),
    )
    return {"message": "Coupon revoked", "code": doc.get("code")}
