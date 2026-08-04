from fastapi import APIRouter, Header, HTTPException, Request
import stripe
from pydantic import BaseModel, Field

from app.admin.audit import log_admin_action
from app.admin.deps import require_admin
from app.admin.permissions import user_can_issue_refunds
from app.config import settings

stripe.api_key = settings.STRIPE_SECRET_KEY

refund_router = APIRouter(prefix="/admin/refunds", tags=["admin-refunds"])


class IssueRefundRequest(BaseModel):
    charge_id: str = Field(min_length=3, max_length=128)
    reason: str | None = Field(default=None, max_length=500)


@refund_router.post("/")
async def issue_refund(
    payload: IssueRefundRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    if not user_can_issue_refunds(admin):
        raise HTTPException(403, "Not allowed to issue refunds")

    refund = stripe.Refund.create(charge=payload.charge_id)

    await log_admin_action(
        admin.get("email") or "",
        "refund_issue",
        target=payload.charge_id,
        meta={
            "refund_id": refund.id,
            "status": refund.status,
            "reason": payload.reason,
        },
    )

    return {
        "refund_id": refund.id,
        "status": refund.status,
    }
