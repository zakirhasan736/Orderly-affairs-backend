from fastapi import APIRouter, Header, HTTPException, Request
from datetime import datetime
import stripe
from app.database import users_collection
from app.security.token_resolver import decode_access_token
from app.config import settings

stripe.api_key = settings.STRIPE_SECRET_KEY

admin_billing_router = APIRouter(prefix="/admin/billing", tags=["admin-billing"])


def require_admin(request: Request, authorization: str | None):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "admin":
        raise HTTPException(403, "Admin only")
    return decoded


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
        users.append({
            "email": u["email"],
            "status": u["billing"]["status"],
            "plan": u["billing"].get("plan"),
            "subscription_id": u["billing"].get("subscription_id"),
            "trial_end": u["billing"].get("trial_end"),
        })

    return users


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
