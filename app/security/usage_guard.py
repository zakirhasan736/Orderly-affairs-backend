from fastapi import HTTPException

PLAN_LIMITS = {
    "monthly": {
        "nextkin": 3,
    },
    "yearly": {
        "nextkin": 10,
    },
}

def enforce_usage(user: dict, resource: str, current_count: int):
    billing = user.get("billing", {})
    
    if billing.get("enterprise"):
        limit = billing.get("enterprise_limits", {}).get(resource)
        if limit is None:
            return
    else:
        plan = billing.get("plan")
        limit = PLAN_LIMITS.get(plan, {}).get(resource)

    if limit is not None and current_count >= limit:
        raise HTTPException(403, f"{resource} limit reached")

