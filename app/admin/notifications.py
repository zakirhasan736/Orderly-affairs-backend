"""Admin broadcast notifications to owners."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field

from app.admin.audit import log_admin_action
from app.admin.deps import require_admin
from app.database import (
    admin_broadcasts_collection,
    admin_notifications_collection,
    users_collection,
)
from app.letters.email_utils import send_email

admin_notifications_router = APIRouter(
    prefix="/admin/notifications",
    tags=["admin-notifications"],
)


class BroadcastRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    audience: Literal["all", "active", "trial", "suspended"] = "all"
    user_emails: Optional[list[EmailStr]] = Field(default=None, max_length=500)


def _audience_query(audience: str) -> dict:
    base: dict = {"role": "owner", "deleted_at": {"$exists": False}}
    if audience == "all":
        return base
    if audience == "active":
        base["billing.status"] = {"$in": ["active", "complimentary"]}
        base["suspended"] = {"$ne": True}
        return base
    if audience == "trial":
        base["billing.status"] = "trialing"
        return base
    if audience == "suspended":
        base["suspended"] = True
        return base
    return base


@admin_notifications_router.post("/broadcast")
async def broadcast_notification(
    payload: BroadcastRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    now = datetime.utcnow()

    emails: list[str] = []
    if payload.user_emails:
        emails = sorted({e.lower().strip() for e in payload.user_emails})
    else:
        cursor = users_collection.find(
            _audience_query(payload.audience),
            {"email": 1},
        )
        async for u in cursor:
            em = (u.get("email") or "").lower().strip()
            if em:
                emails.append(em)

    if not emails:
        raise HTTPException(400, "No recipients matched")

    sent = 0
    failed: list[str] = []
    html = f"<div style='font-family:sans-serif'>{payload.body.replace(chr(10), '<br/>')}</div>"

    for email in emails:
        try:
            await send_email(email, payload.subject, html)
            sent += 1
            await admin_notifications_collection.insert_one(
                {
                    "email": email,
                    "subject": payload.subject,
                    "body": payload.body,
                    "sent_at": now,
                    "sent_by": admin.get("email"),
                    "audience": payload.audience,
                }
            )
        except Exception as exc:
            failed.append(email)
            print(f"admin broadcast failed for {email}: {exc}")

    broadcast_doc = {
        "subject": payload.subject,
        "body": payload.body,
        "audience": payload.audience,
        "recipient_count": len(emails),
        "sent_count": sent,
        "failed_count": len(failed),
        "failed_emails": failed[:50],
        "created_at": now,
        "created_by": admin.get("email"),
    }
    result = await admin_broadcasts_collection.insert_one(broadcast_doc)

    await log_admin_action(
        admin.get("email") or "",
        "notification_broadcast",
        target=str(result.inserted_id),
        meta={
            "audience": payload.audience,
            "sent": sent,
            "failed": len(failed),
        },
    )

    return {
        "message": "Broadcast queued/sent",
        "broadcast_id": str(result.inserted_id),
        "recipient_count": len(emails),
        "sent_count": sent,
        "failed_count": len(failed),
        "failed_emails": failed[:20],
    }


@admin_notifications_router.get("/history")
async def broadcast_history(
    request: Request,
    authorization: str | None = Header(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    await require_admin(request, authorization)
    total = await admin_broadcasts_collection.count_documents({})
    skip = (page - 1) * page_size
    cursor = (
        admin_broadcasts_collection.find({})
        .sort("created_at", -1)
        .skip(skip)
        .limit(page_size)
    )
    items = []
    async for doc in cursor:
        items.append(
            {
                "id": str(doc["_id"]),
                "subject": doc.get("subject"),
                "body": doc.get("body"),
                "audience": doc.get("audience"),
                "recipient_count": doc.get("recipient_count"),
                "sent_count": doc.get("sent_count"),
                "failed_count": doc.get("failed_count"),
                "created_at": doc.get("created_at"),
                "created_by": doc.get("created_by"),
            }
        )
    return {"broadcasts": items, "page": page, "page_size": page_size, "total": total}
