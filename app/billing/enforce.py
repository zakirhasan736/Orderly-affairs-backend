from fastapi import HTTPException

BLOCKED_STATUSES = {"blocked", "past_due", "paused"}

def enforce_billing(user: dict):
    status = user.get("billing", {}).get("status")

    if status in BLOCKED_STATUSES:
        raise HTTPException(
            403,
            "Billing issue. Please update subscription."
        )
