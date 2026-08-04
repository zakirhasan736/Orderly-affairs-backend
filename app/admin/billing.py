from datetime import datetime
from typing import Literal, Optional

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr

from app.admin.deps import require_admin
from app.admin.permissions import user_can_clear_rate_limits
from app.admin.users import apply_complimentary_grant
from app.billing.access import get_comp
from app.config import settings
from app.database import users_collection

stripe.api_key = settings.STRIPE_SECRET_KEY

admin_billing_router = APIRouter(prefix="/admin/billing", tags=["admin-billing"])


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


class ClearRateLimitsRequest(BaseModel):
    email: Optional[EmailStr] = None
    clear_auth_limits: bool = True
    clear_otp_logs: bool = False


@admin_billing_router.get("/overview")
async def billing_overview(
    request: Request,
    authorization: str | None = Header(default=None),
):
    await require_admin(request, authorization)

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
    await require_admin(request, authorization)

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
    admin = await require_admin(request, authorization)
    email = payload.email.lower().strip()

    user = await users_collection.find_one({"email": email, "role": "owner"})
    if not user:
        raise HTTPException(404, "Owner account not found")

    return await apply_complimentary_grant(
        user=user,
        kind=payload.kind,
        duration_days=payload.duration_days,
        duration_months=payload.duration_months,
        duration_years=payload.duration_years,
        note=payload.note,
        send_email=payload.send_email,
        cancel_stripe_subscription=payload.cancel_stripe_subscription,
        granted_by=admin.get("sub") or admin.get("email"),
    )


@admin_billing_router.post("/revoke-complimentary")
async def revoke_complimentary(
    payload: RevokeCompRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    await require_admin(request, authorization)
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


@admin_billing_router.post("/clear-rate-limits")
async def clear_rate_limits(
    payload: ClearRateLimitsRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """
    Unstick a user blocked by inflated rate-limit timers.
    Clears auth_rate_limits docs (and optionally recent OTP send logs).
    """
    admin = await require_admin(request, authorization)
    if not user_can_clear_rate_limits(admin):
        raise HTTPException(403, "Not allowed to clear rate limits")
    from app.database import auth_rate_limits_collection, otp_fraud_logs_collection

    deleted_auth = 0
    deleted_otp = 0

    if payload.clear_auth_limits:
        if payload.email:
            email = payload.email.lower().strip()
            # keys look like "login:user@x.com:1.2.3.4"
            result = await auth_rate_limits_collection.delete_many(
                {"key": {"$regex": email.replace(".", "\\.")}}
            )
            deleted_auth = result.deleted_count or 0
        else:
            result = await auth_rate_limits_collection.delete_many({})
            deleted_auth = result.deleted_count or 0

    if payload.clear_otp_logs and payload.email:
        email = payload.email.lower().strip()
        result = await otp_fraud_logs_collection.delete_many(
            {"email": email, "action": "send"}
        )
        deleted_otp = result.deleted_count or 0

    return {
        "message": "Rate limits cleared",
        "deleted_auth_limit_docs": deleted_auth,
        "deleted_otp_send_logs": deleted_otp,
    }


@admin_billing_router.get("/mrr")
async def admin_mrr(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Platform-wide Stripe MRR — admin only."""
    await require_admin(request, authorization)

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


@admin_billing_router.get("/report")
async def billing_report(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Monthly transaction report + recent invoice ledger from Stripe."""
    await require_admin(request, authorization)

    months: dict[str, dict] = {}
    transactions: list[dict] = []
    disputes = 0
    failed = 0
    current_mrr = 0.0
    current_month = datetime.utcnow().strftime("%Y-%m")

    try:
        paid = stripe.Invoice.list(status="paid", limit=100)
        for inv in paid.data:
            month = datetime.utcfromtimestamp(inv.created).strftime("%Y-%m")
            bucket = months.setdefault(
                month,
                {
                    "month": month,
                    "txns": 0,
                    "gross": 0.0,
                    "refunds": 0.0,
                    "net": 0.0,
                    "mrr": 0.0,
                },
            )
            amount = (inv.amount_paid or 0) / 100
            bucket["txns"] += 1
            bucket["gross"] += amount
            bucket["net"] += amount
            bucket["mrr"] += amount

            if len(transactions) < 50:
                transactions.append(
                    {
                        "date": datetime.utcfromtimestamp(inv.created).isoformat(),
                        "customer": inv.customer_email or "Unknown",
                        "invoice": inv.number or inv.id,
                        "method": "Card",
                        "amount": amount,
                        "status": "paid",
                        "currency": (inv.currency or "usd").upper(),
                    }
                )
    except Exception as exc:
        print(f"billing report paid invoices: {exc}")

    try:
        open_inv = stripe.Invoice.list(status="open", limit=30)
        for inv in open_inv.data:
            failed += 1
            if len(transactions) < 80:
                transactions.append(
                    {
                        "date": datetime.utcfromtimestamp(inv.created).isoformat(),
                        "customer": inv.customer_email or "Unknown",
                        "invoice": inv.number or inv.id,
                        "method": "Card",
                        "amount": (inv.amount_due or 0) / 100,
                        "status": "failed",
                        "currency": (inv.currency or "usd").upper(),
                    }
                )
    except Exception as exc:
        print(f"billing report open invoices: {exc}")

    try:
        # Stripe Dispute list if available
        if hasattr(stripe, "Dispute"):
            for d in stripe.Dispute.list(limit=20).data:
                disputes += 1
                if len(transactions) < 100:
                    transactions.append(
                        {
                            "date": datetime.utcfromtimestamp(d.created).isoformat(),
                            "customer": "Disputed charge",
                            "invoice": d.charge or d.id,
                            "method": "Card",
                            "amount": (d.amount or 0) / 100,
                            "status": "disputed",
                            "currency": (d.currency or "usd").upper(),
                        }
                    )
    except Exception as exc:
        print(f"billing report disputes: {exc}")

    report_rows = []
    sorted_months = sorted(months.keys(), reverse=True)[:6]
    prev_net = None
    for month in reversed(sorted_months):
        row = months[month]
        row["gross"] = round(row["gross"], 2)
        row["refunds"] = round(row["refunds"], 2)
        row["net"] = round(row["net"], 2)
        row["mrr"] = round(row["mrr"], 2)
        if prev_net and prev_net > 0:
            row["delta_pct"] = round(((row["net"] - prev_net) / prev_net) * 100, 1)
        else:
            row["delta_pct"] = None
        prev_net = row["net"]
        report_rows.append(row)
        if month == current_month:
            current_mrr = row["mrr"]

    if not current_mrr and report_rows:
        current_mrr = report_rows[-1]["mrr"]

    transactions.sort(key=lambda t: t["date"], reverse=True)

    return {
        "mrr": current_mrr,
        "net_month": current_mrr,
        "failed": failed,
        "disputes": disputes,
        "monthly": list(reversed(report_rows)),
        "transactions": transactions[:40],
    }
