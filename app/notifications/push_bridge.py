"""
Bridge: send Web Push alongside product emails when the user opted in.

Skips OTP / password / invite-with-credentials flows (callers must not use this).
Respects notification_preferences.push_state == "active" unless force=True.
"""

from __future__ import annotations

from typing import Any

from app.auth.notification_prefs import normalize_notification_prefs
from app.notifications.web_push import (
    default_push_click_url,
    send_web_push_to_email,
    send_web_push_to_user,
    vapid_configured,
)


def _user_wants_push(user: dict[str, Any] | None, *, force: bool) -> bool:
    if not user or not vapid_configured():
        return False
    if force:
        return True

    role = str(user.get("role") or "").lower()
    # Collaborators opt in per-device via PushManager.subscribe — they may not
    # have owner vault push_state. If they have stored subscriptions, deliver.
    if role in {"nextkin", "family", "nok"}:
        subs = user.get("push_subscriptions") or []
        return isinstance(subs, list) and any(
            isinstance(item, dict) and item.get("endpoint") for item in subs
        )

    prefs = normalize_notification_prefs(user.get("notification_preferences"))
    return str(prefs.get("push_state") or "") == "active"


async def notify_web_push(
    user: dict[str, Any] | None,
    *,
    title: str,
    body: str,
    tag: str,
    url: str | None = None,
    force: bool = False,
    urgency: str = "normal",
) -> int:
    """
    Send a browser push to one user document (owner / family / NOK / admin user).
    Returns successful delivery count (0 if skipped / unconfigured / no subs).
    """
    if not _user_wants_push(user, force=force):
        return 0
    return await send_web_push_to_user(
        user,
        title=title,
        body=body,
        url=url or default_push_click_url(user),
        tag=tag,
        urgency=urgency,
    )


async def notify_web_push_email(
    email: str,
    *,
    title: str,
    body: str,
    tag: str,
    url: str | None = None,
    owner_id: str | None = None,
    force: bool = False,
) -> int:
    """Resolve user by email (optionally scoped to owner vault) then push."""
    from bson import ObjectId

    from app.database import users_collection

    email_norm = (email or "").strip().lower()
    if not email_norm or not vapid_configured():
        return 0

    user = None
    if owner_id:
        try:
            oid = ObjectId(owner_id)
        except Exception:
            oid = None
        if oid is not None:
            user = await users_collection.find_one(
                {
                    "email": email_norm,
                    "$or": [
                        {"role": "owner", "_id": oid},
                        {"role": {"$in": ["nextkin", "family"]}, "owner_id": str(owner_id)},
                    ],
                }
            )
    if not user:
        user = await users_collection.find_one({"email": email_norm})

    return await notify_web_push(
        user,
        title=title,
        body=body,
        tag=tag,
        url=url,
        force=force,
    )


# Re-export for callers that already import send helpers
__all__ = [
    "notify_web_push",
    "notify_web_push_email",
    "send_web_push_to_user",
    "send_web_push_to_email",
    "vapid_configured",
]
