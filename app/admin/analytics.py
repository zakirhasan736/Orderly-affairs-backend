"""Admin analytics — anonymised aggregates only (never vault contents)."""

from __future__ import annotations

from calendar import month_abbr
from datetime import datetime

from fastapi import APIRouter, Header, Request

from app.admin.deps import require_admin
from app.database import (
    admin_coupons_collection,
    section_data_collection,
    users_collection,
)

admin_analytics_router = APIRouter(
    prefix="/admin/analytics", tags=["admin-analytics"]
)

# Display labels for known plan codes / kit names
PLAN_LABELS = {
    "standard": "Standard Kit",
    "standard_kit": "Standard Kit",
    "fireproof": "Fireproof Kit",
    "fireproof_kit": "Fireproof Kit",
    "essentials": "Essentials Kit",
    "essentials_kit": "Essentials Kit",
    "portal": "Portal only",
    "portal_only": "Portal only",
    "self_starter": "Self Starter",
    "self-starter": "Self Starter",
    "annual": "Portal Annual",
    "monthly": "Portal Monthly",
    "lifetime": "Lifetime",
    "complimentary": "Complimentary",
    "trial": "Trial",
    "trialing": "Trial",
}

# Representative sections for completion rates (metadata presence only)
SECTION_LABELS = [
    ("1", "Personal Information"),
    ("5", "Insurance Policies"),
    ("2", "Will & Estate Documents"),
    ("3", "Letters to Loved Ones"),
]


def _month_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _month_label(ym: str) -> str:
    try:
        y, m = ym.split("-")
        return month_abbr[int(m)]
    except Exception:
        return ym


@admin_analytics_router.get("")
async def analytics_dashboard(
    request: Request,
    authorization: str | None = Header(default=None),
):
    await require_admin(request, authorization)

    now = datetime.utcnow()
    # Last 6 calendar months including current
    months: list[str] = []
    y, m = now.year, now.month
    for _ in range(6):
        months.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()

    base = {"role": "owner", "deleted_at": {"$exists": False}}
    total_owners = await users_collection.count_documents(base)
    active = await users_collection.count_documents(
        {
            **base,
            "suspended": {"$ne": True},
            "billing.status": {"$in": ["active", "complimentary", "trialing"]},
        }
    )
    trial = await users_collection.count_documents(
        {**base, "billing.status": "trialing"}
    )

    # Monthly signups from created_at
    signup_counts: dict[str, int] = {k: 0 for k in months}
    cursor = users_collection.find(base, {"created_at": 1})
    async for u in cursor:
        created = u.get("created_at")
        if isinstance(created, datetime):
            key = _month_key(created)
            if key in signup_counts:
                signup_counts[key] += 1

    monthly_signups = [
        {
            "month": ym,
            "label": _month_label(ym),
            "count": signup_counts[ym],
        }
        for ym in months
    ]

    # Active vaults by plan
    plan_pipeline = [
        {
            "$match": {
                **base,
                "suspended": {"$ne": True},
                "billing.status": {
                    "$in": ["active", "complimentary", "trialing"]
                },
            }
        },
        {
            "$group": {
                "_id": {
                    "$ifNull": [
                        "$billing.plan",
                        {"$ifNull": ["$billing.status", "unset"]},
                    ]
                },
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"count": -1}},
    ]
    plan_rows = await users_collection.aggregate(plan_pipeline).to_list(30)
    plans = []
    for row in plan_rows:
        raw = str(row["_id"] or "unset").strip()
        key = raw.lower().replace(" ", "_")
        label = PLAN_LABELS.get(key) or PLAN_LABELS.get(raw.lower()) or (
            raw.replace("_", " ").title() if raw != "unset" else "Unassigned"
        )
        plans.append({"plan": label, "count": row["count"]})

    # Section completion: share of owners with a section_data doc for that section
    # (counts only — never decrypts contents)
    section_completion = []
    if total_owners > 0:
        for section_id, label in SECTION_LABELS:
            # Distinct owners with any doc for this section
            owners_with = await section_data_collection.distinct(
                "owner_id",
                {"section_id": {"$in": [section_id, int(section_id)]}},
            )
            # Also try string match on owner_email path — count docs then unique
            pct = round((len(owners_with) / total_owners) * 100)
            section_completion.append(
                {
                    "section_id": section_id,
                    "label": label,
                    "pct": min(100, pct),
                    "owners_with_data": len(owners_with),
                    "attention": pct < 30,
                }
            )
    else:
        for section_id, label in SECTION_LABELS:
            section_completion.append(
                {
                    "section_id": section_id,
                    "label": label,
                    "pct": 0,
                    "owners_with_data": 0,
                    "attention": True,
                }
            )

    unused_coupons = await admin_coupons_collection.count_documents(
        {"status": "unused"}
    )

    # Trial → paid convert estimate: active / (active + trial) when possible
    convert_pct = None
    denom = active + trial
    if denom > 0 and active:
        # Rough: paid-ish among engaged
        paid = await users_collection.count_documents(
            {
                **base,
                "billing.status": {"$in": ["active", "complimentary"]},
            }
        )
        convert_pct = round((paid / denom) * 100)

    return {
        "totals": {
            "users": total_owners,
            "active": active,
            "trial": trial,
            "convert_pct": convert_pct,
            "unused_coupons": unused_coupons,
        },
        "monthly_signups": monthly_signups,
        "plans": plans,
        "section_completion": section_completion,
    }
