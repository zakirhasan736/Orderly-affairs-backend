"""Weekly security monitoring: audit vault encryption health + raise admin alerts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.admin.audit import log_admin_action
from app.config import settings
from app.database import (
    admin_security_alerts_collection,
    auth_rate_limits_collection,
    users_collection,
)
from app.security.security_audit import run_security_audit

scheduler = AsyncIOScheduler()


def _failures_from_audit(results: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for name, payload in (results or {}).items():
        if not isinstance(payload, dict):
            continue
        if payload.get("status") == "error":
            issues.append(f"{name}: audit error ({payload.get('error')})")
            continue
        failed = int(payload.get("failed") or 0)
        plain = int(
            payload.get("plaintext_left")
            or payload.get("plain_title_left")
            or payload.get("plain_sensitive_left")
            or payload.get("plain_users")
            or 0
        )
        if failed:
            issues.append(f"{name}: {failed} decrypt/integrity failures")
        if plain:
            issues.append(f"{name}: {plain} plaintext remnants")
    return issues


async def run_weekly_security_monitor() -> dict[str, Any]:
    """
    Weekly job: encryption integrity audit + lockout / MFA compliance snapshot.
    Writes admin_security_alerts when problems are found; always logs audit trail.
    """
    started = datetime.utcnow()
    audit = await run_security_audit()
    issues = _failures_from_audit(audit)

    locked_accounts = await users_collection.count_documents(
        {
            "role": "owner",
            "$or": [
                {"suspended": True},
                {"billing.status": "blocked"},
            ],
        }
    )
    admins_without_mfa = await users_collection.count_documents(
        {
            "is_admin": True,
            "deleted_at": {"$exists": False},
            "$or": [
                {"admin_mfa_enabled": {"$ne": True}},
                {"admin_mfa_enabled": {"$exists": False}},
            ],
        }
    )
    rate_limit_keys = 0
    try:
        rate_limit_keys = await auth_rate_limits_collection.count_documents({})
    except Exception:
        rate_limit_keys = 0

    summary = {
        "ran_at": started.isoformat() + "Z",
        "issues": issues,
        "issue_count": len(issues),
        "locked_accounts": locked_accounts,
        "admins_without_mfa": admins_without_mfa,
        "auth_rate_limit_docs": rate_limit_keys,
        "audit": audit,
    }

    severity = "high" if issues or admins_without_mfa else "low"
    alert_text = (
        f"Weekly security monitor: {len(issues)} encryption issue(s), "
        f"{admins_without_mfa} admin(s) without MFA, "
        f"{locked_accounts} locked/suspended account(s)."
    )
    if issues:
        alert_text += " Details: " + "; ".join(issues[:8])

    await admin_security_alerts_collection.insert_one(
        {
            "alert": alert_text[:500],
            "severity": severity,
            "target": "weekly_security_monitor",
            "source": "weekly_monitor",
            "meta": {
                "issue_count": len(issues),
                "admins_without_mfa": admins_without_mfa,
                "locked_accounts": locked_accounts,
            },
            "created_at": started,
        }
    )

    await log_admin_action(
        "system@orderly-affairs",
        "security.weekly_monitor",
        target="platform",
        meta={
            "issue_count": len(issues),
            "issues": issues[:20],
            "admins_without_mfa": admins_without_mfa,
            "locked_accounts": locked_accounts,
        },
    )

    print("Weekly security monitor:", alert_text)
    return summary


def start_weekly_security_scheduler() -> None:
    if not getattr(settings, "WEEKLY_SECURITY_MONITOR_ENABLED", True):
        if settings.APP_ENV == "development":
            print("Weekly security monitor disabled")
        return

    day = str(getattr(settings, "WEEKLY_SECURITY_MONITOR_DAY", "sun") or "sun").lower()
    hour = int(getattr(settings, "WEEKLY_SECURITY_MONITOR_HOUR", 4) or 4)
    minute = int(getattr(settings, "WEEKLY_SECURITY_MONITOR_MINUTE", 30) or 30)

    scheduler.add_job(
        run_weekly_security_monitor,
        trigger="cron",
        day_of_week=day,
        hour=hour,
        minute=minute,
        timezone="UTC",
        id="weekly-security-monitor",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    if not scheduler.running:
        scheduler.start()
    if settings.APP_ENV == "development":
        print(
            f"Weekly security monitor scheduled ({day} {hour:02d}:{minute:02d} UTC)"
        )
