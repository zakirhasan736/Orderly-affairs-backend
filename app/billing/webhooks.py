from fastapi import APIRouter, Request, HTTPException
from datetime import datetime
import stripe
from app.notifications.billing_emails import send_billing_email, BillingEmailEvent
from app.database import users_collection
from app.config import settings

stripe.api_key = settings.STRIPE_SECRET_KEY

webhook_router = APIRouter(prefix="/billing", tags=["billing-webhooks"])


# ============================================================
# STRIPE WEBHOOK
# ============================================================

@webhook_router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception:
        raise HTTPException(status_code=400, detail="Webhook error")

    event_type = event["type"]
    data = event["data"]["object"]


    # ========================================================
    # PAYMENT FAILED → PAST_DUE
    # ========================================================
    if event_type in [
        "invoice.payment_failed",
        "customer.subscription.payment_failed",
    ]:
        sub_id = data.get("subscription") or data.get("id")
        now = datetime.utcnow()
        await users_collection.update_one(
            {"billing.subscription_id": sub_id},
            {
                "$set": {
                    "billing.status": "past_due",
                    "billing.lock_reason": "payment_failed",
                    "billing.locked_at": now,
                    "billing.is_trial": False,
                    "updated_at": now,
                }
            },
        )
        try:
            from app.notifications.payment_lock_emails import send_payment_lock_email

            user = await users_collection.find_one({"billing.subscription_id": sub_id})
            if user:
                await send_payment_lock_email(user=user, reason="payment_failed")
        except Exception as exc:
            print(f"payment_failed lock email error: {exc}")

    # ========================================================
    # PAYMENT SUCCESS → ACTIVE
    # ========================================================
    elif event_type == "invoice.payment_succeeded":
        sub_id = data.get("subscription")
        user = await users_collection.find_one(
            {"billing.subscription_id": sub_id}
        )
        # Never overwrite owner-granted complimentary access
        if user and (user.get("billing") or {}).get("status") == "complimentary":
            return {"status": "ok"}

        plan = (user or {}).get("billing", {}).get("plan") or "monthly"
        if user:
            try:
                await send_billing_email(
                    user=user,
                    event=BillingEmailEvent.PLAN_MONTHLY
                    if plan == "monthly"
                    else BillingEmailEvent.PLAN_YEARLY,
                )
            except Exception as exc:
                print(f"invoice.payment_succeeded email error: {exc}")

        await users_collection.update_one(
            {"billing.subscription_id": sub_id},
            {
                "$set": {
                    "billing.status": "active",
                    "billing.is_trial": False,
                    "billing.lock_reason": None,
                    "billing.locked_at": None,
                    "updated_at": datetime.utcnow(),
                }
            },
        )


    # ========================================================
    # SUBSCRIPTION CANCELED → BLOCKED
    # ========================================================
    elif event_type == "customer.subscription.deleted":
        await users_collection.update_one(
            {"billing.subscription_id": data["id"]},
            {
                "$set": {
                    "billing.status": "blocked",
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        
    # ---------------------------------------------------
    # SUBSCRIPTION UPDATED (pause/resume)
    # ---------------------------------------------------
    elif event_type == "customer.subscription.updated":
        pause = data.get("pause_collection")
        # Do not clobber complimentary / past_due / blocked from Stripe "updated"
        user = await users_collection.find_one(
            {"billing.subscription_id": data["id"]}
        )
        current = (user or {}).get("billing", {}).get("status")
        if current in ("complimentary", "past_due", "blocked"):
            return {"status": "ok"}

        await users_collection.update_one(
            {"billing.subscription_id": data["id"]},
            {
                "$set": {
                    "billing.status": "paused" if pause else "active",
                    "updated_at": datetime.utcnow(),
                }
            },
        )

    # ========================================================
    # TRIAL WILL END (Stripe warning, not a charge)
    # ========================================================
    elif event_type == "customer.subscription.trial_will_end":
            await users_collection.update_one(
                {"billing.subscription_id": data["id"]},
                {"$set": {
                    "billing.trial_ending_soon": True,
                    "updated_at": datetime.utcnow(),
                }}
            )

    return {"received": True}
