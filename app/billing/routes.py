from app.security.token_resolver import decode_access_token, decode_owner_or_nok_token
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from typing import Literal, Optional

from datetime import datetime, timedelta
import stripe
from app.notifications.billing_emails import send_billing_email, BillingEmailEvent
from app.database import users_collection
from app.config import settings

class ApplyCouponRequest(BaseModel):
    code: str

class ChangePlanRequest(BaseModel):
    plan: Literal["monthly", "yearly"]


class PauseRequest(BaseModel):
    resume_at: Optional[datetime] = None
# ============================================================
# STRIPE CONFIG
# ============================================================

stripe.api_key = settings.STRIPE_SECRET_KEY

# ============================================================
# ROUTER
# ============================================================

billing_router = APIRouter(prefix="/billing", tags=["billing"])


# ============================================================
# AUTH HELPER
# ============================================================

async def get_owner_from_token(request: Request, authorization: str | None = None):
    decoded = decode_access_token(request, authorization)

    if not decoded or decoded.get("role") != "owner":
        raise HTTPException(status_code=401, detail="Unauthorized")

    user = await users_collection.find_one(
        {"email": decoded["sub"], "role": "owner"}
    )

    if not user:
        raise HTTPException(status_code=404, detail="Owner not found")

    return user


# ============================================================
# MODELS
# ============================================================

class StartSubscriptionRequest(BaseModel):
    plan: Literal["monthly", "yearly"]
    is_trial: bool = False


class ConfirmCardRequest(BaseModel):
    payment_method_id: str

class PauseRequest(BaseModel):
    resume_at: datetime

# ============================================================
# 1️⃣ CREATE STRIPE CUSTOMER
# ============================================================

@billing_router.post("/create-customer")
async def create_customer(request: Request, authorization: str | None = Header(default=None)):
    user = await get_owner_from_token(request, authorization)

    if user["billing"].get("customer_id"):
        return {"customer_id": user["billing"]["customer_id"]}

    customer = stripe.Customer.create(
        email=user["email"],
        name=user.get("full_name"),
        metadata={"user_id": str(user["_id"])}
    )

    await users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"billing.customer_id": customer.id}}
    )

    return {"customer_id": customer.id}


# ============================================================
# 2️⃣ SETUP INTENT (SAVE CARD)
# ============================================================

@billing_router.post("/setup-intent")
async def setup_intent(request: Request, authorization: str | None = Header(default=None)):
    user = await get_owner_from_token(request, authorization)

    customer_id = user["billing"].get("customer_id")
    if not customer_id:
        raise HTTPException(400, "Stripe customer missing")

    intent = stripe.SetupIntent.create(
        customer=customer_id,
        payment_method_types=["card"]
    )

    return {"client_secret": intent.client_secret}


# ============================================================
# 3️⃣ CONFIRM CARD
# ============================================================

@billing_router.post("/confirm-card")
async def confirm_card(
    payload: ConfirmCardRequest,
    request: Request, authorization: str | None = Header(default=None)
):
    user = await get_owner_from_token(request, authorization)

    customer_id = user["billing"].get("customer_id")
    if not customer_id:
        raise HTTPException(400, "Stripe customer missing")

    # 1️⃣ Attach payment method
    stripe.PaymentMethod.attach(
        payload.payment_method_id,
        customer=customer_id,
    )

    # 2️⃣ Set as default
    stripe.Customer.modify(
        customer_id,
        invoice_settings={
            "default_payment_method": payload.payment_method_id
        }
    )

    # 3️⃣ Save flag in DB
    await users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"billing.payment_method_attached": True}}
    )

    return {"message": "Card saved successfully"}

# ============================================================
# 4️⃣ START SUBSCRIPTION (TRIAL OR PAID)
# ============================================================

@billing_router.post("/start-subscription")
async def start_subscription(
    payload: StartSubscriptionRequest,
    request: Request, authorization: str | None = Header(default=None)
):
    user = await get_owner_from_token(request, authorization)
    billing = user["billing"]

    if not billing.get("payment_method_attached") and not payload.is_trial:
        raise HTTPException(403, "Payment method required")


    if billing.get("subscription_id"):
        raise HTTPException(400, "Subscription already exists")

    price_id = (
        settings.STRIPE_PRICE_MONTHLY
        if payload.plan == "monthly"
        else settings.STRIPE_PRICE_YEARLY
    )

    subscription = stripe.Subscription.create(
        customer=billing["customer_id"],
        items=[{"price": price_id}],
        trial_period_days=settings.TRIAL_DAYS if payload.is_trial else None,
        payment_settings={"save_default_payment_method": "on_subscription"},
    )
    await send_billing_email(
            user=user,
            event=BillingEmailEvent.WELCOME
        )
    # AFTER subscription creation
    if payload.is_trial:
        await send_billing_email(
            user=user,
            event=BillingEmailEvent.PLAN_TRIAL
        )
    elif payload.plan == "monthly":
        await send_billing_email(
            user=user,
            event=BillingEmailEvent.PLAN_MONTHLY
        )
    else:
        await send_billing_email(
            user=user,
            event=BillingEmailEvent.PLAN_YEARLY
        )
    now = datetime.utcnow()
    update = {
        "billing.subscription_id": subscription.id,
        "billing.plan": payload.plan,
        "billing.is_trial": payload.is_trial,
        "billing.status": "trialing" if payload.is_trial else "active",
    }

    if payload.is_trial:
        update["billing.trial_start"] = now
        update["billing.trial_end"] = now + timedelta(days=settings.TRIAL_DAYS)

    await users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": update}
    )

    return {
        "subscription_id": subscription.id,
        "status": update["billing.status"],
        "trial_end": update.get("billing.trial_end"),
    }

# Upgrade / Downgrade Plans

# Goal: let owners switch monthly ↔ yearly without canceling.
# @billing_router.post("/change-plan")
# async def change_plan(
#     payload: ChangePlanRequest,
#     request: Request, authorization: str | None = Header(default=None)
# ):
#     user = await get_owner_from_token(request, authorization)
#     billing = user["billing"]

#     if not billing.get("subscription_id"):
#         raise HTTPException(400, "No active subscription")

#     subscription = stripe.Subscription.retrieve(billing["subscription_id"])

#         # auto-resume if paused
#     if subscription.get("pause_collection"):
#             stripe.Subscription.modify(
#                 subscription.id,
#                 pause_collection=None
#             )


#     new_price_id = (
#         settings.STRIPE_PRICE_MONTHLY
#         if payload.plan == "monthly"
#         else settings.STRIPE_PRICE_YEARLY
#     )

#     # 🔹 End trial immediately if still trialing
#     stripe.Subscription.modify(
#         subscription.id,
#         trial_end="now",
#         items=[{
#             "id": subscription["items"]["data"][0].id,
#             "price": new_price_id,
#         }],
#         proration_behavior="create_prorations",
#     )

#     # 🔹 Update DB immediately
#     await users_collection.update_one(
#         {"_id": user["_id"]},
#         {"$set": {
#             "billing.plan": payload.plan,
#             "billing.is_trial": False,
#             "billing.status": "active",
#             "billing.trial_end": None,
#             "updated_at": datetime.utcnow(),
#         }}
#     )

#     # 🔹 Send ONE email (not in webhook)
#     await send_billing_email(
#         user=user,
#         event=BillingEmailEvent.PLAN_MONTHLY
#         if payload.plan == "monthly"
#         else BillingEmailEvent.PLAN_YEARLY
#     )

#     return {
#         "message": "Plan updated successfully",
#         "new_plan": payload.plan,
#     }
@billing_router.post("/change-plan")
async def change_plan(
    payload: ChangePlanRequest,
    request: Request, authorization: str | None = Header(default=None)
):
    user = await get_owner_from_token(request, authorization)
    billing = user["billing"]

    if not billing.get("subscription_id"):
        raise HTTPException(400, "No active subscription")

    new_price_id = (
        settings.STRIPE_PRICE_MONTHLY
        if payload.plan == "monthly"
        else settings.STRIPE_PRICE_YEARLY
    )

    # 🔥 ONE atomic Stripe call
    stripe.Subscription.modify(
        billing["subscription_id"],
        pause_collection=None,           # ← unpause
        trial_end="now",
        items=[{
            "id": stripe.Subscription.retrieve(
                billing["subscription_id"]
            )["items"]["data"][0].id,
            "price": new_price_id,
        }],
        proration_behavior="create_prorations",
    )

    await users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "billing.plan": payload.plan,
            "billing.is_trial": False,
            "billing.status": "active",
            "billing.trial_end": None,
            "billing.resume_at": None,
            "updated_at": datetime.utcnow(),
        }}
    )

    await send_billing_email(
        user=user,
        event=(
            BillingEmailEvent.PLAN_MONTHLY
            if payload.plan == "monthly"
            else BillingEmailEvent.PLAN_YEARLY
        )
    )

    return {
        "message": "Plan updated successfully",
        "new_plan": payload.plan,
    }


# Billing Portal (Self-Service)

# Let users:

# update card

# view invoices

# cancel subscription

# resume subscription

# All hosted by Stripe.

@billing_router.post("/portal")
async def billing_portal(request: Request, authorization: str | None = Header(default=None)):
    user = await get_owner_from_token(request, authorization)

    customer_id = user["billing"].get("customer_id")
    if not customer_id:
        raise HTTPException(400, "Stripe customer missing")

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=settings.FRONTEND_URL + "/settings/billing",
    )

    return {"url": session.url}

# Invoices In-App

# Expose Stripe invoices inside your app.
@billing_router.get("/invoices")
async def list_invoices(request: Request, authorization: str | None = Header(default=None)):
    user = await get_owner_from_token(request, authorization)
    customer_id = user["billing"].get("customer_id")

    if not customer_id:
        raise HTTPException(400, "Stripe customer missing")

    invoices = stripe.Invoice.list(customer=customer_id, limit=20)

    return [
        {
            "id": inv.id,
            "amount_due": inv.amount_due / 100,
            "currency": inv.currency,
            "status": inv.status,
            "hosted_invoice_url": inv.hosted_invoice_url,
            "pdf": inv.invoice_pdf,
            "created": inv.created,
        }
        for inv in invoices.data
    ]

# Coupons / Promo Codes

# Supports:

# % discounts

# fixed discounts

# trial extensions

# enterprise deals
@billing_router.post("/apply-coupon")
async def apply_coupon(
    payload: ApplyCouponRequest,
    request: Request, authorization: str | None = Header(default=None)
):
    user = await get_owner_from_token(request, authorization)
    sub_id = user["billing"].get("subscription_id")

    if not sub_id:
        raise HTTPException(400, "No subscription")

    stripe.Subscription.modify(
        sub_id,
        coupon=payload.code,
    )

    return {"message": "Coupon applied"}

@billing_router.post("/pause")
async def pause_subscription(
    request: Request,
    authorization: str | None = Header(default=None),
    payload: PauseRequest | None = None,
):
    user = await get_owner_from_token(request, authorization)
    sub_id = user["billing"].get("subscription_id")

    stripe.Subscription.modify(
        sub_id,
        pause_collection={"behavior": "mark_uncollectible"}
    )

    resume_at = payload.resume_at if payload else None

    await users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "billing.status": "paused",
            "billing.resume_at": resume_at,
            "updated_at": datetime.utcnow()
        }}
    )

    return {"status": "paused", "resume_at": resume_at}



@billing_router.post("/resume")
async def resume_subscription(request: Request, authorization: str | None = Header(default=None)):
    user = await get_owner_from_token(request, authorization)
    sub_id = user["billing"].get("subscription_id")

    stripe.Subscription.modify(
        sub_id,
        pause_collection=None
    )

    await users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": {
            "billing.status": "active",
            "updated_at": datetime.utcnow()
        }}
    )

    return {"status": "active"}


@billing_router.get("/status")
async def billing_status(request: Request, authorization: str | None = Header(default=None)):
    user = await get_owner_from_token(request, authorization)
    billing = user.get("billing", {})

    return {
        "status": billing.get("status"),
        "plan": billing.get("plan"),
        "is_trial": billing.get("is_trial"),
        "trial_end": billing.get("trial_end"),
        "has_payment_method": billing.get("payment_method_attached"),
    }

