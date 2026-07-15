"""Owner billing / complimentary-access helpers used by auth + cron."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException

PAYMENT_LOCK_MESSAGE = (
    "Your vault access is paused due to a payment or plan issue. "
    "You can still sign in to update your card or activate a plan. "
    "Please also check email on this account for messages from Orderly Affairs."
)

# Statuses that may use the full product
ACTIVE_STATUSES = {"trialing", "active", "complimentary", "paused"}

# Soft redirect to plan picker (session allowed, full vault after plan starts)
NEEDS_PLAN_STATUSES = {"pending"}

# Can sign in, but vault is locked until payment is fixed
BILLING_ONLY_STATUSES = {"blocked", "past_due", "unpaid"}


def _as_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def default_billing_fields() -> dict[str, Any]:
    return {
        "customer_id": None,
        "subscription_id": None,
        "status": "pending",
        "plan": None,
        "is_trial": False,
        "trial_start": None,
        "trial_end": None,
        "trial_mode": None,  # cardless | card_on_file
        "payment_method_attached": False,
        "auto_renew": True,
        "payment_fail_reminders_sent": [],
        "comp": {
            "enabled": False,
            "kind": None,
            "starts_at": None,
            "ends_at": None,
            "granted_by": None,
            "granted_at": None,
            "note": None,
            "reminders_sent": [],
        },
    }


def get_comp(billing: dict | None) -> dict[str, Any]:
    billing = billing or {}
    comp = billing.get("comp") or {}
    return {
        "enabled": bool(comp.get("enabled")),
        "kind": comp.get("kind"),
        "starts_at": _as_naive_utc(comp.get("starts_at")),
        "ends_at": _as_naive_utc(comp.get("ends_at")),
        "granted_by": comp.get("granted_by"),
        "granted_at": _as_naive_utc(comp.get("granted_at")),
        "note": comp.get("note"),
        "reminders_sent": list(comp.get("reminders_sent") or []),
    }


def is_complimentary_active(billing: dict | None, *, now: datetime | None = None) -> bool:
    now = now or datetime.utcnow()
    comp = get_comp(billing)
    if not comp["enabled"]:
        return False
    if comp["kind"] == "lifetime":
        return True
    ends_at = comp["ends_at"]
    if ends_at is None:
        return False
    return ends_at > now


def complimentary_expired(billing: dict | None, *, now: datetime | None = None) -> bool:
    now = now or datetime.utcnow()
    comp = get_comp(billing)
    if not comp["enabled"]:
        return False
    if comp["kind"] == "lifetime":
        return False
    ends_at = comp["ends_at"]
    return ends_at is not None and ends_at <= now


def requires_billing_setup(billing: dict | None) -> bool:
    """True when owner still needs to pick a plan (pending signup)."""
    billing = billing or {}
    if is_complimentary_active(billing):
        return False
    if is_billing_only(billing):
        # Already had a plan/trial — send them to payment repair, not first-time picker
        return False
    status = billing.get("status") or "pending"
    return status in NEEDS_PLAN_STATUSES


def is_billing_only(billing: dict | None, *, now: datetime | None = None) -> bool:
    """
    Vault locked; login allowed so they can fix payment / activate a plan.
    """
    now = now or datetime.utcnow()
    billing = billing or {}

    if is_complimentary_active(billing, now=now):
        return False

    if complimentary_expired(billing, now=now):
        return True

    status = billing.get("status") or "pending"
    if status in BILLING_ONLY_STATUSES:
        return True

    if billing.get("is_trial") and status == "trialing":
        trial_end = _as_naive_utc(billing.get("trial_end"))
        if trial_end is not None and trial_end <= now:
            return True

    return False


# Back-compat alias used by older call sites
def is_billing_locked(billing: dict | None, *, now: datetime | None = None) -> bool:
    return is_billing_only(billing, now=now)


def ensure_owner_login_allowed(user: dict) -> None:
    """Login is always allowed for owners; vault scope is controlled via billing_only."""
    return


def enforce_vault_access(user: dict) -> None:
    """Call from non-billing owner APIs: block vault until payment is fixed."""
    billing = user.get("billing") or {}
    if is_billing_only(billing):
        raise HTTPException(status_code=403, detail=PAYMENT_LOCK_MESSAGE)


def billing_session_flags(billing: dict | None) -> dict[str, Any]:
    billing = billing or {}
    only = is_billing_only(billing)
    return {
        "billing_status": billing.get("status", "pending"),
        "requires_billing": requires_billing_setup(billing),
        "billing_only": only,
        "billing_locked": only,  # alias for older clients
        "is_complimentary": is_complimentary_active(billing),
        "comp_kind": get_comp(billing).get("kind"),
        "comp_ends_at": get_comp(billing).get("ends_at"),
        "trial_mode": billing.get("trial_mode"),
        "auto_renew": billing.get("auto_renew", True),
        "has_payment_method": bool(billing.get("payment_method_attached")),
        "lock_message": PAYMENT_LOCK_MESSAGE if only else None,
    }


def compute_comp_end(
    *,
    kind: str,
    duration_days: int | None = None,
    duration_months: int | None = None,
    duration_years: int | None = None,
    starts_at: datetime | None = None,
) -> datetime | None:
    starts_at = starts_at or datetime.utcnow()
    if kind == "lifetime":
        return None

    if duration_days and duration_days > 0:
        return starts_at + timedelta(days=duration_days)
    if duration_months and duration_months > 0:
        return starts_at + timedelta(days=30 * duration_months)
    if duration_years and duration_years > 0:
        return starts_at + timedelta(days=365 * duration_years)

    raise HTTPException(
        status_code=400,
        detail="Provide duration_days, duration_months, or duration_years for duration comps.",
    )
