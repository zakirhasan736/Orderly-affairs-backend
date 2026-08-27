"""Owner-initiated living NOK access: NOK is emailed as soon as the owner grants."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.database import users_collection
from app.notifications.nextkin_emails import NextKinEmailEvent, send_nextkin_email
from app.notifications.owner_nok_alerts import notify_owner_access_released

# Kept at zero: leftover pending grants from the old delay still flush via the scheduler.
IMMEDIATE_ACCESS_EMAIL_DELAY = timedelta(minutes=0)
LIVING_CREDENTIAL_TTL = timedelta(days=7)
LIVING_REMINDER_AFTER = timedelta(days=3, hours=12)


def pending_immediate_access_filter(*, now: datetime | None = None) -> dict:
    when = now or datetime.utcnow()
    return {
        "role": "nextkin",
        "immediate_access_pending": True,
        "immediate_access": {"$ne": True},
        "access_revoked": {"$ne": True},
        "immediate_access_email_at": {"$lte": when},
    }


async def begin_owner_immediate_access_grant(
    *,
    nextkin: dict,
    owner: dict,
    notify_owner: bool = True,
) -> dict:
    """Confirm living access. Owner is notified; the NOK login email goes out now."""
    if nextkin.get("immediate_access"):
        return {"status": "already_live"}

    now = datetime.utcnow()
    if notify_owner and not nextkin.get("immediate_access_pending"):
        await notify_owner_access_released(owner=owner)

    await _activate_living_access_and_email(nextkin=nextkin, owner=owner, now=now)
    return {"status": "live", "immediate_access": True}


async def _activate_living_access_and_email(
    *,
    nextkin: dict,
    owner: dict,
    now: datetime | None = None,
) -> bool:
    from app.security.nextkin_profile_crypto import load_nextkin_profile

    when = now or datetime.utcnow()
    claimed = await users_collection.update_one(
        {
            "_id": nextkin["_id"],
            "role": "nextkin",
            "access_revoked": {"$ne": True},
            "immediate_access": {"$ne": True},
        },
        {
            "$set": {
                "access_timing": "immediate",
                "immediate_access": True,
                "immediate_access_pending": False,
                "immediate_access_granted_at": when,
                "living_access_state": "notified",
                "living_notified_at": when,
                "living_release_confirmed_at": when,
                "living_credential_expires_at": when + LIVING_CREDENTIAL_TTL,
                "living_reminder_due_at": when + LIVING_REMINDER_AFTER,
                "nok_letter_received": False,
                "access_revoked": False,
                "updated_at": when,
            },
            "$unset": {"immediate_access_email_at": ""},
        },
    )
    if getattr(claimed, "modified_count", 0) != 1:
        return False

    refreshed = load_nextkin_profile(
        await users_collection.find_one({"_id": nextkin["_id"]})
    )
    if not refreshed:
        return False
    nextkin.update(refreshed)
    try:
        await send_nextkin_email(
            event=NextKinEmailEvent.ACCESS_APPROVED,
            nextkin=refreshed,
            owner=owner,
            plain_password=refreshed.get("master_password"),
        )
    except Exception as exc:
        print(f"⚠️ Immediate-access email failed for {nextkin.get('_id')}: {exc}")
        return False
    return True


async def cancel_pending_immediate_access(nextkin_id) -> None:
    now = datetime.utcnow()
    await users_collection.update_one(
        {"_id": nextkin_id},
        {
            "$set": {
                "immediate_access": False,
                "immediate_access_pending": False,
                "living_access_state": "revoked",
                "living_revoked_at": now,
                "updated_at": now,
            },
            "$unset": {
                "immediate_access_email_at": "",
                "living_credential_expires_at": "",
                "living_reminder_due_at": "",
            },
        },
    )


async def complete_due_immediate_access_grants(*, limit: int = 50) -> int:
    """Flush leftover delayed grants (from the old 10-minute window)."""
    now = datetime.utcnow()
    sent = 0
    cursor = users_collection.find(pending_immediate_access_filter(now=now)).limit(
        limit
    )
    async for nk in cursor:
        owner = None
        owner_id = nk.get("owner_id")
        if owner_id:
            from bson import ObjectId
            from bson.errors import InvalidId

            try:
                owner = await users_collection.find_one(
                    {"_id": ObjectId(str(owner_id)), "role": "owner"}
                )
            except (InvalidId, TypeError):
                owner = None
        if not owner:
            print(
                f"⚠️ Immediate-access email skipped: owner missing for {nk.get('_id')}"
            )
            continue
        if await _activate_living_access_and_email(nextkin=nk, owner=owner, now=now):
            sent += 1
    return sent


async def send_due_living_access_reminders(*, limit: int = 50) -> int:
    now = datetime.utcnow()
    sent = 0
    cursor = users_collection.find(
        {
            "role": "nextkin",
            "immediate_access": True,
            "access_revoked": {"$ne": True},
            "living_access_state": "notified",
            "living_reminder_sent_at": {"$exists": False},
            "living_reminder_due_at": {"$lte": now},
            "living_credential_expires_at": {"$gt": now},
            "$or": [{"login_count": {"$exists": False}}, {"login_count": {"$lt": 1}}],
        }
    ).limit(limit)
    async for nk in cursor:
        claimed = await users_collection.update_one(
            {
                "_id": nk["_id"],
                "living_reminder_sent_at": {"$exists": False},
                "living_access_state": "notified",
            },
            {"$set": {"living_reminder_sent_at": now, "updated_at": now}},
        )
        if getattr(claimed, "modified_count", 0) != 1:
            continue
        from app.security.nextkin_profile_crypto import load_nextkin_profile
        from bson import ObjectId

        refreshed = load_nextkin_profile(await users_collection.find_one({"_id": nk["_id"]}))
        if not refreshed:
            continue
        owner = None
        try:
            owner = await users_collection.find_one(
                {"_id": ObjectId(str(refreshed.get("owner_id"))), "role": "owner"}
            )
        except Exception:
            owner = None
        if not owner:
            continue
        try:
            await send_nextkin_email(
                event=NextKinEmailEvent.ACCESS_REMINDER,
                nextkin=refreshed,
                owner=owner,
                plain_password=refreshed.get("master_password"),
            )
            sent += 1
        except Exception as exc:
            print(f"⚠️ Living-access reminder failed for {nk.get('_id')}: {exc}")
    return sent


async def expire_unused_living_credentials(*, limit: int = 50) -> int:
    now = datetime.utcnow()
    result = await users_collection.update_many(
        {
            "role": "nextkin",
            "immediate_access": True,
            "living_access_state": "notified",
            "living_credential_expires_at": {"$lte": now},
            "$or": [{"login_count": {"$exists": False}}, {"login_count": {"$lt": 1}}],
        },
        {
            "$set": {
                "immediate_access": False,
                "living_access_state": "expired",
                "updated_at": now,
            }
        },
    )
    return int(getattr(result, "modified_count", 0) or 0)


async def expire_unused_living_access_if_due(user: dict) -> bool:
    """Return True when unused 7-day credentials have lapsed (login should fail)."""
    if not user or user.get("living_access_state") != "notified":
        return False
    if int(user.get("login_count") or 0) >= 1:
        return False
    expires = user.get("living_credential_expires_at")
    if not expires:
        return False
    if getattr(expires, "tzinfo", None):
        expires = expires.replace(tzinfo=None)
    if expires > datetime.utcnow():
        return False
    await users_collection.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "immediate_access": False,
                "living_access_state": "expired",
                "updated_at": datetime.utcnow(),
            }
        },
    )
    return True


async def mark_living_access_active(nextkin_id) -> None:
    now = datetime.utcnow()
    await users_collection.update_one(
        {"_id": nextkin_id, "living_access_state": "notified"},
        {
            "$set": {
                "living_access_state": "active",
                "living_activated_at": now,
                "updated_at": now,
            },
            "$unset": {
                "living_credential_expires_at": "",
                "living_reminder_due_at": "",
            },
        },
    )

