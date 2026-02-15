from fastapi import APIRouter, Header, HTTPException
import stripe
from app.config import settings
from app.security.jwt_handler import verify_token

stripe.api_key = settings.STRIPE_SECRET_KEY

refund_router = APIRouter(prefix="/admin/refunds", tags=["admin-refunds"])

def require_admin(authorization: str):
    token = authorization.split(" ")[1]
    decoded = verify_token(token)
    if not decoded or decoded.get("role") != "admin":
        raise HTTPException(403, "Admin only")


@refund_router.post("/")
async def issue_refund(
    charge_id: str,
    authorization: str = Header(...)
):
    require_admin(authorization)

    refund = stripe.Refund.create(charge=charge_id)

    return {
        "refund_id": refund.id,
        "status": refund.status,
    }
