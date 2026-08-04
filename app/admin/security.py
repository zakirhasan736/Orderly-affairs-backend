"""Security overview — MFA compliance, lockouts, alert feed."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Optional

from bson import ObjectId
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.admin.audit import log_admin_action
from app.admin.deps import require_admin
from app.config import settings
from app.database import (
    admin_audit_logs_collection,
    admin_security_alerts_collection,
    auth_rate_limits_collection,
    users_collection,
)

admin_security_router = APIRouter(prefix="/admin/security", tags=["admin-security"])


class CreateAlertRequest(BaseModel):
    alert: str = Field(min_length=3, max_length=500)
    severity: Literal["low", "medium", "high"] = "medium"
    target: Optional[str] = None


def _alert_serial(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "alert": doc.get("alert"),
        "severity": doc.get("severity") or "low",
        "target": doc.get("target"),
        "created_at": doc.get("created_at"),
        "source": doc.get("source") or "manual",
    }


@admin_security_router.get("/overview")
async def security_overview(
    request: Request,
    authorization: str | None = Header(default=None),
):
    await require_admin(request, authorization)
    now = datetime.utcnow()
    since_24h = now - timedelta(hours=24)
    since_7d = now - timedelta(days=7)

    # Failed logins approximated from auth rate-limit docs touched recently
    failed_24h = 0
    try:
        failed_24h = await auth_rate_limits_collection.count_documents(
            {
                "key": {"$regex": "^login:"},
                "$or": [
                    {"updated_at": {"$gte": since_24h}},
                    {"created_at": {"$gte": since_24h}},
                    {"blocked_until": {"$gte": since_24h}},
                ],
            }
        )
    except Exception:
        failed_24h = 0

    locked_accounts = await users_collection.count_documents(
        {
            "role": "owner",
            "$or": [
                {"suspended": True},
                {"billing.status": "blocked"},
                {"billing.lock_reason": {"$exists": True, "$ne": None}},
            ],
        }
    )

    admins = await users_collection.find(
        {"is_admin": True, "deleted_at": {"$exists": False}},
        {"email": 1, "full_name": 1, "admin_mfa_enabled": 1, "admin_role": 1},
    ).to_list(200)
    admins_without_mfa = [
        {
            "email": a.get("email"),
            "full_name": a.get("full_name"),
            "admin_role": a.get("admin_role"),
        }
        for a in admins
        if not a.get("admin_mfa_enabled")
    ]

    high_alerts = await admin_security_alerts_collection.count_documents(
        {"severity": "high", "created_at": {"$gte": since_7d}}
    )

    # Also count high-severity from audit if no manual alerts yet
    if high_alerts == 0:
        high_alerts = await admin_audit_logs_collection.count_documents(
            {
                "action": {
                    "$in": [
                        "user.force_logout",
                        "user.suspend",
                        "auth.lockout",
                        "legacy.deny",
                    ]
                },
                "created_at": {"$gte": since_7d},
            }
        )

    alerts_cursor = (
        admin_security_alerts_collection.find({})
        .sort("created_at", -1)
        .limit(40)
    )
    alerts = [_alert_serial(doc) async for doc in alerts_cursor]

    # Synthesize recent alerts from audit when empty
    if not alerts:
        audit = (
            await admin_audit_logs_collection.find(
                {
                    "action": {
                        "$regex": "^(user\\.|auth\\.|legacy\\.|admin\\.login)"
                    }
                }
            )
            .sort("created_at", -1)
            .limit(20)
            .to_list(20)
        )
        for doc in audit:
            action = doc.get("action") or ""
            severity = "low"
            if "suspend" in action or "lock" in action or "deny" in action:
                severity = "high"
            elif "force_logout" in action or "login" in action:
                severity = "medium"
            alerts.append(
                {
                    "id": str(doc["_id"]),
                    "alert": f"{action}: {doc.get('target') or '—'}",
                    "severity": severity,
                    "target": doc.get("target"),
                    "created_at": doc.get("created_at"),
                    "source": "audit",
                }
            )

    return {
        "failed_logins_24h": failed_24h,
        "failed_threshold": 50,
        "locked_accounts": locked_accounts,
        "admins_without_mfa": len(admins_without_mfa),
        "admins_without_mfa_list": admins_without_mfa,
        "high_severity_7d": high_alerts,
        "alerts": alerts,
        "weekly_monitor_enabled": bool(settings.WEEKLY_SECURITY_MONITOR_ENABLED),
        "pillars_doc": "docs/SECURITY_PILLARS.md",
    }


@admin_security_router.post("/weekly-monitor/run")
async def run_weekly_monitor_now(
    request: Request,
    authorization: str | None = Header(default=None),
):
    from app.admin.deps import require_system_owner
    from app.security.weekly_monitor import run_weekly_security_monitor

    admin = await require_system_owner(request, authorization)
    result = await run_weekly_security_monitor()
    await log_admin_action(
        admin.get("email") or "",
        "security.weekly_monitor_manual",
        target="platform",
        meta={"issue_count": result.get("issue_count")},
    )
    # Strip bulky nested audit for API response
    return {
        "ran_at": result.get("ran_at"),
        "issue_count": result.get("issue_count"),
        "issues": result.get("issues"),
        "locked_accounts": result.get("locked_accounts"),
        "admins_without_mfa": result.get("admins_without_mfa"),
        "auth_rate_limit_docs": result.get("auth_rate_limit_docs"),
    }


@admin_security_router.post("/alerts")
async def create_alert(
    payload: CreateAlertRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    doc = {
        "alert": payload.alert.strip(),
        "severity": payload.severity,
        "target": payload.target,
        "source": "manual",
        "created_at": datetime.utcnow(),
        "created_by": admin.get("email"),
    }
    result = await admin_security_alerts_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    await log_admin_action(
        admin.get("email") or "",
        "security.alert",
        payload.target,
        {"severity": payload.severity},
    )
    return _alert_serial(doc)


@admin_security_router.delete("/alerts/{alert_id}")
async def delete_alert(
    alert_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await require_admin(request, authorization)
    if not ObjectId.is_valid(alert_id):
        raise HTTPException(400, "Invalid id")
    result = await admin_security_alerts_collection.delete_one(
        {"_id": ObjectId(alert_id)}
    )
    if not result.deleted_count:
        raise HTTPException(404, "Alert not found")
    await log_admin_action(admin.get("email") or "", "security.alert_delete", alert_id)
    return {"message": "deleted"}
