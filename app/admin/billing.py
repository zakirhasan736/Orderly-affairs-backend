from datetime import datetime
from typing import Literal, Optional

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr

from app.billing.access import compute_comp_end, default_billing_fields, get_comp
from app.config import settings
from app.database import users_collection
from app.notifications.comp_emails import CompEmailEvent, send_comp_email
from app.security.token_resolver import decode_access_token

stripe.api_key = settings.STRIPE_SECRET_KEY

admin_billing_router = APIRouter(prefix="/admin/billing", tags=["admin-billing"])


def require_admin(request: Request, authorization: str | None):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "admin":
        raise HTTPException(403, "Admin only")
    return decoded


class GrantCompRequest(BaseModel):
    email: EmailStr
    kind: Literal["lifetime", "duration"]
    # Use one of these for duration comps
    duration_days: Optional[int] = None
    duration_months: Optional[int] = None
    duration_years: Optional[int] = None
    note: Optional[str] = None
    send_email: bool = True
    # If true, cancel any active Stripe subscription so they aren't billed
    cancel_stripe_subscription: bool = True


class RevokeCompRequest(BaseModel):
    email: EmailStr
    lock_account: bool = False
    note: Optional[str] = None


@admin_billing_router.get("/overview")
async def billing_overview(
    request: Request,
    authorization: str | None = Header(default=None),
):
    require_admin(request, authorization)

    pipeline = [
        {"$group": {
            "_id": "$billing.status",
            "count": {"$sum": 1}
        }}
    ]

    stats = await users_collection.aggregate(pipeline).to_list(None)
    return {"stats": stats}


@admin_billing_router.get("/users")
async def billing_users(
    request: Request,
    authorization: str | None = Header(default=None),
):
    require_admin(request, authorization)

    cursor = users_collection.find(
        {"role": "owner"},
        {
            "email": 1,
            "billing": 1,
            "created_at": 1,
        }
    )

    users = []
    async for u in cursor:
        billing = u.get("billing") or {}
        comp = get_comp(billing)
        users.append({
            "email": u["email"],
            "status": billing.get("status"),
            "plan": billing.get("plan"),
            "subscription_id": billing.get("subscription_id"),
            "trial_end": billing.get("trial_end"),
            "is_complimentary": comp["enabled"] and (
                comp["kind"] == "lifetime"
                or (comp["ends_at"] and comp["ends_at"] > datetime.utcnow())
            ),
            "comp_kind": comp["kind"],
            "comp_ends_at": comp["ends_at"],
            "payment_method_attached": billing.get("payment_method_attached"),
        })

    return users


@admin_billing_router.post("/grant-complimentary")
async def grant_complimentary(
    payload: GrantCompRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """
    System owner grants free access instead of (or after) the normal trial.

    - lifetime: full free access, no reminder emails, never auto-expires
    - duration: free until ends_at; reminders at ~30d / 7d / 1d before end
    """
    admin = require_admin(request, authorization)
    email = payload.email.lower().strip()
    now = datetime.utcnow()

    user = await users_collection.find_one({"email": email, "role": "owner"})
    if not user:
        raise HTTPException(404, "Owner account not found")

    ends_at = compute_comp_end(
        kind=payload.kind,
        duration_days=payload.duration_days,
        duration_months=payload.duration_months,
        duration_years=payload.duration_years,
        starts_at=now,
    )

    billing = user.get("billing") or default_billing_fields()
    sub_id = billing.get("subscription_id")

    if payload.cancel_stripe_subscription and sub_id:
        try:
            stripe.Subscription.delete(sub_id)
        except Exception as exc:
            print(f"grant-comp: could not cancel Stripe sub {sub_id}: {exc}")

    update = {
        "billing.status": "complimentary",
        "billing.is_trial": False,
        "billing.plan": "complimentary",
        "billing.subscription_id": None if payload.cancel_stripe_subscription else sub_id,
        "billing.lock_reason": None,
        "billing.locked_at": None,
        "billing.comp.enabled": True,
        "billing.comp.kind": payload.kind,
        "billing.comp.starts_at": now,
        "billing.comp.ends_at": ends_at,
        "billing.comp.granted_by": admin.get("sub") or admin.get("email"),
        "billing.comp.granted_at": now,
        "billing.comp.note": payload.note,
        "billing.comp.reminders_sent": [],
        "updated_at": now,
    }

    await users_collection.update_one({"_id": user["_id"]}, {"$set": update})

    if payload.send_email:
        updated = await users_collection.find_one({"_id": user["_id"]})
        try:
            await send_comp_email(
                user=updated or user,
                event=CompEmailEvent.GRANTED,
                ends_at=ends_at,
            )
        except Exception as exc:
            print(f"grant-comp email failed for {email}: {exc}")

    return {
        "message": "Complimentary access granted",
        "email": email,
        "kind": payload.kind,
        "ends_at": ends_at,
        "status": "complimentary",
    }


@admin_billing_router.post("/revoke-complimentary")
async def revoke_complimentary(
    payload: RevokeCompRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    require_admin(request, authorization)
    email = payload.email.lower().strip()
    now = datetime.utcnow()

    user = await users_collection.find_one({"email": email, "role": "owner"})
    if not user:
        raise HTTPException(404, "Owner account not found")

    new_status = "blocked" if payload.lock_account else "pending"
    await users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "billing.status": new_status,
                "billing.plan": None,
                "billing.comp.enabled": False,
                "billing.lock_reason": "comp_revoked" if payload.lock_account else None,
                "billing.locked_at": now if payload.lock_account else None,
                "billing.comp.note": payload.note,
                "updated_at": now,
            }
        },
    )

    return {
        "message": "Complimentary access revoked",
        "email": email,
        "status": new_status,
    }


@admin_billing_router.get("/mrr")
async def admin_mrr(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Platform-wide Stripe MRR — admin only."""
    require_admin(request, authorization)

    invoices = stripe.Invoice.list(status="paid", limit=100)
    mrr: dict[str, float] = {}

    for inv in invoices.data:
        month = datetime.utcfromtimestamp(inv.created).strftime("%Y-%m")
        mrr.setdefault(month, 0)
        mrr[month] += inv.amount_paid / 100

    return sorted(
        [{"month": k, "revenue": v} for k, v in mrr.items()],
        key=lambda x: x["month"],
    )
