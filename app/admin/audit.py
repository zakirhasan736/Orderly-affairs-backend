"""Admin audit log helper."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.database import admin_audit_logs_collection


async def log_admin_action(
    admin_email: str,
    action: str,
    target: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    await admin_audit_logs_collection.insert_one(
        {
            "admin_email": (admin_email or "").lower().strip(),
            "action": action,
            "target": target,
            "meta": meta or {},
            "created_at": datetime.utcnow(),
        }
    )
