"""Append-only vault access audit trail."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.security.vault_billing_middleware import _pick_decoded
from app.security.vault_paths import (
    _SKIP_METHODS,
    extract_section_id_from_path,
    is_vault_api_path,
)

logger = logging.getLogger(__name__)

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _action_for(method: str) -> str:
    if method in _WRITE_METHODS:
        return "write"
    return "read"


def _client_ip(request: Request) -> str | None:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client:
        return request.client.host
    return None


async def log_vault_event(
    *,
    actor_id: str | None,
    actor_role: str | None,
    owner_id: str | None,
    method: str,
    path: str,
    status_code: int,
    ip: str | None = None,
    access_type: str | None = None,
    portal_role: str | None = None,
    section_id: str | None = None,
    success: bool | None = None,
    detail: str | None = None,
) -> None:
    from app.database import vault_audit_logs_collection

    if success is None:
        success = status_code < 400

    doc: dict[str, Any] = {
        "actor_id": actor_id,
        "actor_role": actor_role,
        "access_type": access_type,
        "portal_role": portal_role,
        "owner_id": owner_id,
        "method": method,
        "path": path,
        "action": _action_for(method),
        "section_id": section_id or extract_section_id_from_path(path),
        "status_code": status_code,
        "success": success,
        "ip": ip,
        "detail": detail,
        "timestamp": datetime.now(timezone.utc),
    }
    try:
        await vault_audit_logs_collection.insert_one(doc)
    except Exception as exc:
        logger.warning("vault audit log failed: %s", exc)
        return

    if int(status_code) == 403:
        await maybe_alert_403_burst()


class VaultAuditMiddleware(BaseHTTPMiddleware):
    """Record vault API access after each request completes."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in _SKIP_METHODS:
            return await call_next(request)

        path = request.url.path
        if not is_vault_api_path(path):
            return await call_next(request)

        decoded = _pick_decoded(request)
        response = await call_next(request)

        actor_id = None
        actor_role = None
        access_type = None
        portal_role = None
        owner_id = None

        if decoded:
            actor_id = str(decoded.get("sub") or decoded.get("email") or "")
            actor_role = decoded.get("role")
            access_type = decoded.get("access_type")
            portal_role = decoded.get("portal_role")
            owner_id = decoded.get("owner_id")
            if actor_role == "owner" and not owner_id:
                owner_id = actor_id

        await log_vault_event(
            actor_id=actor_id or None,
            actor_role=actor_role,
            owner_id=str(owner_id) if owner_id else None,
            method=request.method,
            path=path,
            status_code=response.status_code,
            ip=_client_ip(request),
            access_type=access_type,
            portal_role=portal_role,
            section_id=extract_section_id_from_path(path),
        )
        return response


async def ensure_vault_audit_indexes() -> None:
    """Keep vault_audit_logs at least 12 months, then TTL-expire."""
    from app.config import settings
    from app.database import vault_audit_logs_collection

    days = max(365, int(getattr(settings, "VAULT_AUDIT_RETENTION_DAYS", 400) or 400))
    expire = int(days * 86400)
    try:
        await vault_audit_logs_collection.create_index(
            [("timestamp", 1)],
            expireAfterSeconds=expire,
            name="vault_audit_ttl",
        )
    except Exception as exc:
        logger.warning("vault audit TTL index: %s", exc)
        try:
            await vault_audit_logs_collection.drop_index("vault_audit_ttl")
            await vault_audit_logs_collection.create_index(
                [("timestamp", 1)],
                expireAfterSeconds=expire,
                name="vault_audit_ttl",
            )
        except Exception as inner:
            logger.warning("vault audit TTL recreate failed: %s", inner)
    try:
        await vault_audit_logs_collection.create_index(
            [("status_code", 1), ("timestamp", -1)],
            name="vault_audit_status_time",
        )
    except Exception as exc:
        logger.warning("vault audit status index: %s", exc)


async def maybe_alert_403_burst() -> None:
    from datetime import timedelta

    from app.config import settings
    from app.database import (
        admin_security_alerts_collection,
        vault_audit_logs_collection,
    )

    window = int(getattr(settings, "VAULT_AUDIT_403_WINDOW_SECONDS", 300) or 300)
    threshold = int(getattr(settings, "VAULT_AUDIT_403_BURST_THRESHOLD", 25) or 25)
    since = datetime.now(timezone.utc) - timedelta(seconds=window)
    try:
        count = await vault_audit_logs_collection.count_documents(
            {"status_code": 403, "timestamp": {"$gte": since}}
        )
    except Exception as exc:
        logger.warning("403 burst count failed: %s", exc)
        return
    if count < threshold:
        return
    cooldown = since
    try:
        existing = await admin_security_alerts_collection.find_one(
            {"kind": "vault_403_burst", "created_at": {"$gte": cooldown}}
        )
        if existing:
            return
        await admin_security_alerts_collection.insert_one(
            {
                "kind": "vault_403_burst",
                "severity": "high",
                "count": count,
                "window_seconds": window,
                "threshold": threshold,
                "created_at": datetime.now(timezone.utc),
                "message": (
                    f"{count} HTTP 403 vault responses in {window}s "
                    "(possible probing or broken grants)."
                ),
            }
        )
        logger.warning(
            "SOC2 alert: vault 403 burst count=%s window=%ss", count, window
        )
    except Exception as exc:
        logger.warning("403 burst alert failed: %s", exc)
