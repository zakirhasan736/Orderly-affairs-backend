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
    plan: Literal["monthly", "yearly", "essentials", "advantage"]


class PauseRequest(BaseModel):
    resume_at: Optional[datetime] = None
# ============================================================
# STRIPE CONFIG
# ============================================================

stripe.api_key = settings.STRIPE_SECRET_KEY


def resolve_stripe_price_id(plan: str) -> str:
    """
    Map plan id → Stripe Price id.
    Essentials / Advantage are annual signup tiers.
    Set STRIPE_PRICE_ESSENTIALS / STRIPE_PRICE_ADVANTAGE when ready;
    until then both fall back to STRIPE_PRICE_YEARLY.
    """
    essentials = (settings.STRIPE_PRICE_ESSENTIALS or "").strip() or settings.STRIPE_PRICE_YEARLY
    advantage = (settings.STRIPE_PRICE_ADVANTAGE or "").strip() or settings.STRIPE_PRICE_YEARLY
    mapping = {
        "essentials": essentials,
        "yearly": settings.STRIPE_PRICE_YEARLY,
        "advantage": advantage,
        "monthly": settings.STRIPE_PRICE_MONTHLY,
    }
    price_id = mapping.get(plan)
    if not price_id:
        raise HTTPException(400, "Invalid plan")
    return price_id

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
    plan: Literal["monthly", "yearly", "essentials", "advantage"]
    is_trial: bool = False
    # cardless = no card now; card_on_file = verify card now, charge after trial
    trial_mode: Literal["cardless", "card_on_file"] | None = None


class AutoRenewRequest(BaseModel):
    enabled: bool


class ConfirmCardRequest(BaseModel):
    payment_method_id: str

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

    trial_mode = payload.trial_mode
    if payload.is_trial:
        trial_mode = trial_mode or "cardless"
        if trial_mode == "card_on_file" and not billing.get("payment_method_attached"):
            raise HTTPException(
                403,
                "Add and verify a card to avoid interruption after the trial. "
                "No charge today — billing starts when the trial ends.",
            )
    elif not billing.get("payment_method_attached"):
        raise HTTPException(403, "Payment method required")

    if billing.get("subscription_id"):
        raise HTTPException(400, "Subscription already exists")

    if not billing.get("customer_id"):
        raise HTTPException(400, "Create a Stripe customer first")

    price_id = resolve_stripe_price_id(payload.plan)

    try:
        subscription = stripe.Subscription.create(
            customer=billing["customer_id"],
            items=[{"price": price_id}],
            trial_period_days=settings.TRIAL_DAYS if payload.is_trial else None,
            payment_settings={"save_default_payment_method": "on_subscription"},
            # Auto-renew on by default; user can turn off from settings later
            cancel_at_period_end=False,
        )
    except stripe.error.InvalidRequestError as exc:
        # Return a proper HTTP error (with CORS) instead of an unhandled 500.
        raise HTTPException(status_code=400, detail=str(exc.user_message or exc)) from exc
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
        "billing.auto_renew": True,
        "billing.lock_reason": None,
        "billing.locked_at": None,
        "billing.payment_fail_reminders_sent": [],
    }

    if payload.is_trial:
        update["billing.trial_start"] = now
        update["billing.trial_end"] = now + timedelta(days=settings.TRIAL_DAYS)
        update["billing.trial_mode"] = trial_mode
    else:
        update["billing.trial_mode"] = None

    await users_collection.update_one(
        {"_id": user["_id"]},
        {"$set": update}
    )

    return {
        "subscription_id": subscription.id,
        "status": update["billing.status"],
        "trial_end": update.get("billing.trial_end"),
        "trial_mode": update.get("billing.trial_mode"),
        "auto_renew": True,
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

    new_price_id = resolve_stripe_price_id(payload.plan)

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
            "billing.lock_reason": None,
            "billing.locked_at": None,
            "billing.payment_fail_reminders_sent": [],
            "billing.auto_renew": True,
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
    from app.billing.access import billing_session_flags

    user = await get_owner_from_token(request, authorization)
    billing = user.get("billing", {})
    flags = billing_session_flags(billing)

    return {
        "status": billing.get("status"),
        "plan": billing.get("plan"),
        "is_trial": billing.get("is_trial"),
        "trial_end": billing.get("trial_end"),
        "trial_mode": billing.get("trial_mode"),
        "has_payment_method": billing.get("payment_method_attached"),
        "auto_renew": billing.get("auto_renew", True),
        "billing_only": flags["billing_only"],
        "requires_billing": flags["requires_billing"],
        "lock_message": flags["lock_message"],
        "is_complimentary": flags["is_complimentary"],
        "comp_ends_at": flags["comp_ends_at"],
    }


@billing_router.post("/auto-renew")
async def set_auto_renew(
    payload: AutoRenewRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """
    Toggle Stripe cancel_at_period_end.
    Offline = avoid auto-charge at trial end / next renewal.
    Online = allow automatic charge.
    """
    user = await get_owner_from_token(request, authorization)
    billing = user.get("billing", {})
    sub_id = billing.get("subscription_id")
    if not sub_id:
        raise HTTPException(400, "No active subscription")

    try:
        stripe.Subscription.modify(
            sub_id,
            cancel_at_period_end=not payload.enabled,
        )
    except Exception as exc:
        raise HTTPException(400, f"Could not update auto-renew: {exc}") from exc

    await users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "billing.auto_renew": payload.enabled,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    return {
        "auto_renew": payload.enabled,
        "message": (
            "Auto-renew is on. Your card will be charged when the current period ends."
            if payload.enabled
            else "Auto-renew is off. You will not be charged automatically; "
            "access may pause when the current period ends unless you pay."
        ),
    }


@billing_router.post("/attach-card-during-trial")
async def attach_card_during_trial(
    payload: ConfirmCardRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """Cardless trial users can add a card mid-trial to avoid interruption."""
    user = await get_owner_from_token(request, authorization)
    billing = user.get("billing", {})
    customer_id = billing.get("customer_id")
    if not customer_id:
        raise HTTPException(400, "No billing customer")

    stripe.PaymentMethod.attach(payload.payment_method_id, customer=customer_id)
    stripe.Customer.modify(
        customer_id,
        invoice_settings={"default_payment_method": payload.payment_method_id},
    )

    update: dict = {
        "billing.payment_method_attached": True,
        "billing.trial_mode": "card_on_file",
        "updated_at": datetime.utcnow(),
    }
    await users_collection.update_one({"_id": user["_id"]}, {"$set": update})

    return {
        "message": "Card saved. No charge today — it will be used when the trial ends if auto-renew is on.",
        "trial_mode": "card_on_file",
        "has_payment_method": True,
    }

