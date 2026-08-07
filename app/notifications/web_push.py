"""
Web Push (VAPID) helpers.

Requires settings.VAPID_PUBLIC_KEY + settings.VAPID_PRIVATE_KEY.
Subscriptions are stored on each user document as `push_subscriptions`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.config import settings
from app.database import users_collection


def vapid_configured() -> bool:
    return bool(
        (settings.VAPID_PUBLIC_KEY or "").strip()
        and (settings.VAPID_PRIVATE_KEY or "").strip()
    )


def get_vapid_public_key() -> str | None:
    key = (settings.VAPID_PUBLIC_KEY or "").strip()
    return key or None


def _normalize_vapid_private_key(raw: str | None) -> str:
    """
    Accept PEM from AWS SSM / .env as either real newlines or literal \\n
    (same pattern as JWT_PRIVATE_KEY). Do not wrap in quotes in Parameter Store.
    """
    if not raw:
        return ""
    text = str(raw).strip()
    # Strip accidental surrounding quotes from copy-paste
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        text = text[1:-1].strip()
    if "BEGIN" in text and "\\n" in text:
        text = text.replace("\\n", "\n")
    return text.strip()


def normalize_subscription(raw: dict[str, Any]) -> dict[str, Any] | None:
    endpoint = str(raw.get("endpoint") or "").strip()
    keys = raw.get("keys") if isinstance(raw.get("keys"), dict) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint or not p256dh or not auth:
        return None
    return {
        "endpoint": endpoint,
        "keys": {"p256dh": p256dh, "auth": auth},
        "created_at": raw.get("created_at") or datetime.utcnow().isoformat(),
        "user_agent": (str(raw.get("user_agent") or "").strip()[:240] or None),
    }


async def upsert_push_subscription(user_id, subscription: dict[str, Any]) -> None:
    normalized = normalize_subscription(subscription)
    if not normalized:
        raise ValueError("Invalid push subscription")

    user = await users_collection.find_one({"_id": user_id})
    existing = list(user.get("push_subscriptions") or []) if user else []
    next_subs: list[dict[str, Any]] = []
    replaced = False
    for item in existing:
        if not isinstance(item, dict):
            continue
        if item.get("endpoint") == normalized["endpoint"]:
            next_subs.append({**normalized, "created_at": item.get("created_at") or normalized["created_at"]})
            replaced = True
        else:
            next_subs.append(item)
    if not replaced:
        next_subs.append(normalized)
    # Cap device list
    next_subs = next_subs[-12:]

    await users_collection.update_one(
        {"_id": user_id},
        {
            "$set": {
                "push_subscriptions": next_subs,
                "updated_at": datetime.utcnow(),
            }
        },
    )


async def remove_push_subscription(user_id, endpoint: str) -> None:
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        return
    user = await users_collection.find_one({"_id": user_id})
    if not user:
        return
    existing = [
        item
        for item in (user.get("push_subscriptions") or [])
        if isinstance(item, dict) and item.get("endpoint") != endpoint
    ]
    await users_collection.update_one(
        {"_id": user_id},
        {
            "$set": {
                "push_subscriptions": existing,
                "updated_at": datetime.utcnow(),
            }
        },
    )


def _send_one(subscription: dict[str, Any], payload: dict[str, Any]) -> str | None:
    """
    Returns None on success, or 'gone' if the subscription should be removed.
    """
    if not vapid_configured():
        return "unconfigured"

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("⚠️ pywebpush not installed — skipping Web Push send")
        return "unconfigured"

    try:
        webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                "keys": subscription["keys"],
            },
            data=json.dumps(payload),
            vapid_private_key=_normalize_vapid_private_key(settings.VAPID_PRIVATE_KEY),
            vapid_claims={
                "sub": (settings.VAPID_SUBJECT or "mailto:support@orderly-affairs.com").strip()
            },
            ttl=12 * 60 * 60,
        )
        return None
    except Exception as exc:
        # pywebpush raises WebPushException with response
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            return "gone"
        print(f"⚠️ Web Push send failed: {exc}")
        return "error"


async def send_web_push_to_user(
    user: dict[str, Any] | None,
    *,
    title: str,
    body: str,
    url: str | None = None,
    tag: str | None = None,
) -> int:
    """Send to all stored subscriptions for a user. Returns successful send count."""
    if not user or not vapid_configured():
        return 0

    subs = [
        item
        for item in (user.get("push_subscriptions") or [])
        if isinstance(item, dict) and item.get("endpoint") and item.get("keys")
    ]
    if not subs:
        return 0

    frontend = (settings.FRONTEND_URL or "").rstrip("/")
    payload = {
        "title": title or settings.APP_NAME or "Orderly Affairs",
        "body": body or "",
        "url": url or f"{frontend}/dashboard",
        "tag": tag or "orderly-reminder",
    }

    sent = 0
    gone_endpoints: list[str] = []
    for sub in subs:
        result = _send_one(sub, payload)
        if result is None:
            sent += 1
        elif result == "gone":
            gone_endpoints.append(str(sub.get("endpoint")))

    if gone_endpoints:
        remaining = [
            item
            for item in subs
            if item.get("endpoint") not in gone_endpoints
        ]
        await users_collection.update_one(
            {"_id": user["_id"]},
            {"$set": {"push_subscriptions": remaining}},
        )

    return sent


async def send_web_push_to_email(
    email: str,
    *,
    title: str,
    body: str,
    url: str | None = None,
    tag: str | None = None,
    owner_id: str | None = None,
) -> int:
    from bson import ObjectId

    email = (email or "").strip().lower()
    if not email:
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
                    "email": email,
                    "$or": [
                        {"role": "owner", "_id": oid},
                        {"role": "nextkin", "owner_id": owner_id},
                    ],
                }
            )
    if not user:
        user = await users_collection.find_one({"email": email})

    return await send_web_push_to_user(
        user, title=title, body=body, url=url, tag=tag
    )
