from fastapi import APIRouter, Header, HTTPException, Request
import stripe
from app.config import settings
from app.security.token_resolver import decode_access_token

stripe.api_key = settings.STRIPE_SECRET_KEY

refund_router = APIRouter(prefix="/admin/refunds", tags=["admin-refunds"])


def require_admin(request: Request, authorization: str | None):
    decoded = decode_access_token(request, authorization)
    if decoded.get("role") != "admin":
        raise HTTPException(403, "Admin only")


@refund_router.post("/")
async def issue_refund(
    charge_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    require_admin(request, authorization)

    refund = stripe.Refund.create(charge=charge_id)

    return {
        "refund_id": refund.id,
        "status": refund.status,
    }
