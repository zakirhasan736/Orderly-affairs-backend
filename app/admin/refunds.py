from fastapi import APIRouter, Header, Request
import stripe
from app.admin.deps import require_admin
from app.config import settings

stripe.api_key = settings.STRIPE_SECRET_KEY

refund_router = APIRouter(prefix="/admin/refunds", tags=["admin-refunds"])


@refund_router.post("/")
async def issue_refund(
    charge_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    await require_admin(request, authorization)

    refund = stripe.Refund.create(charge=charge_id)

    return {
        "refund_id": refund.id,
        "status": refund.status,
    }
