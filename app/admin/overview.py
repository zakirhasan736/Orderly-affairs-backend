"""Admin overview stats and audit log."""

from __future__ import annotations

from datetime import datetime

import stripe
from fastapi import APIRouter, Header, Query, Request

from app.admin.deps import require_admin
from app.config import settings
from app.database import admin_audit_logs_collection, users_collection

stripe.api_key = settings.STRIPE_SECRET_KEY

admin_overview_router = APIRouter(prefix="/admin", tags=["admin-overview"])


@admin_overview_router.get("/overview")
async def admin_overview(
    request: Request,
    authorization: str | None = Header(default=None),
):
    await require_admin(request, authorization)

    base = {"role": "owner", "deleted_at": {"$exists": False}}
    total_users = await users_collection.count_documents(base)
    active = await users_collection.count_documents(
        {
            **base,
            "suspended": {"$ne": True},
            "billing.status": {"$in": ["active", "complimentary"]},
        }
    )
    trial = await users_collection.count_documents(
        {**base, "billing.status": "trialing"}
    )
    suspended = await users_collection.count_documents(
        {"role": "owner", "suspended": True}
    )
    complimentary = await users_collection.count_documents(
        {**base, "billing.status": "complimentary"}
    )
    pending = await users_collection.count_documents(
        {**base, "billing.status": "pending"}
    )

    mrr_estimate = None
    try:
        invoices = stripe.Invoice.list(status="paid", limit=50)
        month_key = datetime.utcnow().strftime("%Y-%m")
        month_total = 0.0
        for inv in invoices.data:
            created = datetime.utcfromtimestamp(inv.created).strftime("%Y-%m")
            if created == month_key:
                month_total += inv.amount_paid / 100
        mrr_estimate = round(month_total, 2)
    except Exception as exc:
        print(f"admin overview mrr: {exc}")

    recent = (
        await admin_audit_logs_collection.find({})
        .sort("created_at", -1)
        .limit(20)
        .to_list(20)
    )
    recent_audit = [
        {
            "id": str(doc["_id"]),
            "admin_email": doc.get("admin_email"),
            "action": doc.get("action"),
            "target": doc.get("target"),
            "meta": doc.get("meta") or {},
            "created_at": doc.get("created_at"),
        }
        for doc in recent
    ]

    return {
        "users": total_users,
        "active": active,
        "trial": trial,
        "suspended": suspended,
        "complimentary": complimentary,
        "pending": pending,
        "mrr": mrr_estimate,
        "recent_audit": recent_audit,
    }


@admin_overview_router.get("/audit")
async def admin_audit_log(
    request: Request,
    authorization: str | None = Header(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    action: str | None = Query(default=None),
):
    await require_admin(request, authorization)
    query: dict = {}
    if action:
        query["action"] = action

    total = await admin_audit_logs_collection.count_documents(query)
    skip = (page - 1) * page_size
    cursor = (
        admin_audit_logs_collection.find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(page_size)
    )
    items = []
    async for doc in cursor:
        items.append(
            {
                "id": str(doc["_id"]),
                "admin_email": doc.get("admin_email"),
                "action": doc.get("action"),
                "target": doc.get("target"),
                "meta": doc.get("meta") or {},
                "created_at": doc.get("created_at"),
            }
        )
    return {"audit": items, "page": page, "page_size": page_size, "total": total}
