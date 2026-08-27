"""After-death access policy (pure). Living NOK release does not import this.

Authorization model:

  eligibleForAdminRelease =
    certificateOnFile
    and protectionPeriodCompleted (168 hours from first certificate store)
    and claimantDiditStatus == APPROVED (at least one eligible claimant)
    and not ownerDisputed
    and (ownerDeathCheckStatus == MATCH or validAuthorizedAdminOverride)

Attorney / executor / trustee: Didit ID+selfie must be Approved before they
report a passing or upload a certificate. Declined / in-review / error goes
to manual review. Named next of kin may report and upload first; they must
be Didit Approved before a claim is issued.

SSDMF (Didit usa_states_death_check) runs on the vault owner only, after the
certificate is stored. MATCH corroborates mortality; NO_MATCH / ERROR /
INCONCLUSIVE require manual review or a documented admin override.
NO_MATCH is not proof the owner is alive.

Even when eligible, access is not granted until an admin clicks Release.
No single signal releases a vault.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# Fixed 168-hour owner protection. Not calendar-date + 7.
OWNER_PROTECTION_PERIOD = timedelta(hours=168)
OWNER_NOTICE_REMINDER_OFFSETS = (
    timedelta(hours=48),
    timedelta(hours=96),
    timedelta(hours=144),
)
ADMIN_RELEASE_SLA = timedelta(hours=24)
MANUAL_REVIEW_SLA = timedelta(hours=48)
CLAIM_TTL = timedelta(hours=72)

TERMINAL_STATUSES = frozenset(
    {
        "OWNER_DISPUTED",
        "REJECTED",
        "CLOSED",
    }
)
OPEN_STATUSES = frozenset(
    {
        "DRAFT",
        "AWAITING_CERTIFICATE",
        "CERTIFICATE_RECEIVED",
        "CLAIMANT_KYC_PENDING",
        "DEATH_CHECK_PENDING",
        "PROTECTION_PERIOD_ACTIVE",
        "MANUAL_REVIEW",
        "AWAITING_ADMIN_RELEASE",
        "FRAUD_REVIEW",
        "ACCESS_RELEASED",
        "CLAIM_PENDING",
        "CLAIM_SENT",
        "CLAIM_REDEEMED",
    }
)

DIDIT_APPROVED = "APPROVED"
DIDIT_MAP = {
    "APPROVED": "APPROVED",
    "NOT STARTED": "NOT_STARTED",
    "NOT_STARTED": "NOT_STARTED",
    "IN PROGRESS": "IN_PROGRESS",
    "IN_PROGRESS": "IN_PROGRESS",
    "AWAITING USER": "IN_PROGRESS",
    "RESUBMITTED": "IN_PROGRESS",
    "IN REVIEW": "IN_REVIEW",
    "IN_REVIEW": "IN_REVIEW",
    "DECLINED": "DECLINED",
    "ABANDONED": "ABANDONED",
    "EXPIRED": "ABANDONED",
    "ERROR": "ERROR",
}

SSDMF_MATCH = "MATCH"
SSDMF_NO_MATCH = "NO_MATCH"
SSDMF_INCONCLUSIVE = "INCONCLUSIVE"
SSDMF_ERROR = "ERROR"
SSDMF_PENDING = "PENDING"


def as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def protection_expires_at(started_at: datetime) -> datetime:
    start = as_utc(started_at) or started_at
    return start + OWNER_PROTECTION_PERIOD


def protection_completed(*, started_at: Any, expires_at: Any, now: datetime | None = None) -> bool:
    stamp = as_utc(now) or datetime.now(timezone.utc)
    expires = as_utc(expires_at)
    if expires is None:
        started = as_utc(started_at)
        if started is None:
            return False
        expires = protection_expires_at(started)
    return stamp >= expires


def remaining_protection(expires_at: Any, now: datetime | None = None) -> timedelta:
    stamp = as_utc(now) or datetime.now(timezone.utc)
    expires = as_utc(expires_at)
    if expires is None:
        return OWNER_PROTECTION_PERIOD
    delta = expires - stamp
    return delta if delta.total_seconds() > 0 else timedelta(0)


def normalize_didit(raw: str | None) -> str:
    key = str(raw or "").strip().upper().replace("-", "_")
    key = " ".join(key.split())
    collapsed = key.replace(" ", "_")
    if collapsed in DIDIT_MAP:
        return DIDIT_MAP[collapsed]
    spaced = key.replace("_", " ")
    if spaced in DIDIT_MAP:
        return DIDIT_MAP[spaced]
    if not key:
        return "NOT_STARTED"
    return "ERROR" if "ERR" in collapsed else "IN_PROGRESS"


def didit_is_approved(raw: str | None) -> bool:
    return normalize_didit(raw) == DIDIT_APPROVED


def didit_needs_manual_review(raw: str | None) -> bool:
    return normalize_didit(raw) in {"DECLINED", "IN_REVIEW", "ABANDONED", "ERROR"}


def normalize_ssdmf(raw: str | None) -> str:
    key = str(raw or "").strip().upper().replace(" ", "_")
    if key in {SSDMF_MATCH, "FULL_MATCH", "HIT", "FOUND"}:
        return SSDMF_MATCH
    if key in {SSDMF_NO_MATCH, "NOT_FOUND", "MISS", "CLEAR"}:
        return SSDMF_NO_MATCH
    if key in {SSDMF_PENDING, "NOT_RUN", "INCOMPLETE"}:
        return SSDMF_PENDING
    if key in {SSDMF_INCONCLUSIVE, "UNKNOWN", "AMBIGUOUS"}:
        return SSDMF_INCONCLUSIVE
    if key in {SSDMF_ERROR, "FAIL", "FAILED"}:
        return SSDMF_ERROR
    if not key:
        return SSDMF_PENDING
    return SSDMF_INCONCLUSIVE


def ssdmf_needs_manual_review(status: str | None) -> bool:
    return normalize_ssdmf(status) in {SSDMF_NO_MATCH, SSDMF_INCONCLUSIVE, SSDMF_ERROR}


def derive_case_status(case: dict) -> str:
    if case.get("owner_disputed"):
        return "OWNER_DISPUTED"
    if case.get("status") in TERMINAL_STATUSES:
        return str(case.get("status"))
    if case.get("admin_release"):
        if case.get("claim_redeemed_at"):
            return "CLAIM_REDEEMED"
        if case.get("claim_issued_at"):
            return "CLAIM_SENT"
        return "ACCESS_RELEASED"
    if case.get("manual_review_required") and not case.get("manual_review_resolved_at"):
        return "MANUAL_REVIEW"
    cert = bool(case.get("certificate_id") or case.get("certificate_uploaded_at"))
    if not cert:
        return "AWAITING_CERTIFICATE"
    death = normalize_ssdmf(case.get("owner_death_check_status"))
    if death == SSDMF_PENDING:
        return "DEATH_CHECK_PENDING"
    if not protection_completed(
        started_at=case.get("owner_notice_started_at"),
        expires_at=case.get("owner_notice_expires_at"),
    ):
        return "PROTECTION_PERIOD_ACTIVE"
    return "AWAITING_ADMIN_RELEASE"


def release_gates(
    *,
    case: dict,
    claimants: list[dict],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Authoritative after-death gates. Living release must never call this."""
    stamp = as_utc(now) or datetime.now(timezone.utc)
    disputed = bool(case.get("owner_disputed"))
    cert = bool(case.get("certificate_id") or case.get("certificate_uploaded_at"))
    period_done = protection_completed(
        started_at=case.get("owner_notice_started_at"),
        expires_at=case.get("owner_notice_expires_at"),
        now=stamp,
    )
    death = normalize_ssdmf(case.get("owner_death_check_status"))
    override = bool(case.get("death_check_override"))
    death_ok = death == SSDMF_MATCH or override
    approved = [
        c
        for c in claimants
        if didit_is_approved(c.get("didit_status"))
        and not c.get("access_revoked")
    ]
    claimant_ok = bool(approved)
    frozen = disputed or str(case.get("status") or "") in TERMINAL_STATUSES
    eligible = (
        cert
        and period_done
        and claimant_ok
        and death_ok
        and not frozen
        and not case.get("admin_release")
    )
    remaining = remaining_protection(case.get("owner_notice_expires_at"), stamp)
    clock_started = as_utc(case.get("owner_notice_started_at")) is not None
    if not clock_started:
        remaining = OWNER_PROTECTION_PERIOD
    reasons: list[str] = []
    if not cert:
        reasons.append("Death certificate is not on file.")
    if not clock_started:
        reasons.append("The 168-hour owner protection period starts when the death certificate is stored.")
    elif not period_done:
        reasons.append("The 168-hour owner protection period is still running.")
    if not claimant_ok:
        reasons.append("No claimant has a verified Didit identity (Approved).")
    if not death_ok:
        reasons.append(
            "Owner death-record check is not MATCH (override required for "
            "NO_MATCH / inconclusive / error). This is not proof the owner is alive."
        )
    if disputed:
        reasons.append("The owner disputed this request. Release is frozen.")
    return {
        "certificate_on_file": cert,
        "protection_started": clock_started,
        "protection_period_completed": period_done,
        "protection_remaining_seconds": max(0, int(remaining.total_seconds())),
        "claimant_didit_approved": claimant_ok,
        "approved_claimant_ids": [str(c.get("_id") or c.get("id") or "") for c in approved],
        "owner_disputed": disputed,
        "owner_death_check_status": death,
        "death_check_override": override,
        "owner_death_check_ok": death_ok,
        "eligible_for_admin_release": eligible,
        "frozen": frozen,
        "reasons": reasons,
        "now": stamp,
    }


def reminder_slot(elapsed: timedelta) -> int | None:
    """Return 2, 4, or 6 when that reminder is due."""
    hours = elapsed.total_seconds() / 3600
    if hours >= 144:
        return 6
    if hours >= 96:
        return 4
    if hours >= 48:
        return 2
    return None
