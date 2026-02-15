from fastapi import HTTPException

def enforce_billing(user: dict):
    billing = user.get("billing", {})
    status = billing.get("status")

    if status in ["blocked", "past_due"]:
        raise HTTPException(
            status_code=403,
            detail="Billing issue. Please update payment method."
        )
