from fastapi import HTTPException

# Product caps (per owner). Enterprise may override via enterprise_limits.
PRODUCT_LIMITS = {
    "nextkin": 5,
    "family": 5,
}

# Legacy plan map kept for enterprise-adjacent tooling; product caps above win
# unless enterprise_limits explicitly set.
PLAN_LIMITS = {
    "monthly": {
        "nextkin": 5,
        "family": 5,
    },
    "yearly": {
        "nextkin": 5,
        "family": 5,
    },
}


def resolve_limit(user: dict, resource: str) -> int | None:
    billing = user.get("billing", {}) or {}

    if billing.get("enterprise"):
        limit = (billing.get("enterprise_limits") or {}).get(resource)
        if limit is None:
            return None
        return int(limit)

    if resource in PRODUCT_LIMITS:
        return int(PRODUCT_LIMITS[resource])

    plan = billing.get("plan")
    limit = PLAN_LIMITS.get(plan, {}).get(resource)
    return int(limit) if limit is not None else None


def enforce_usage(user: dict, resource: str, current_count: int):
    limit = resolve_limit(user, resource)

    if limit is not None and current_count >= limit:
        label = "Next-of-Kin" if resource == "nextkin" else resource
        if resource == "family":
            label = "Family members"
        raise HTTPException(
            403,
            f"{label} limit reached (maximum {limit})",
        )
