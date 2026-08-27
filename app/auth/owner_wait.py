"""Owner protection clock display. After-death authorization uses AfterDeathAccessCase timestamps (168 hours). Login is not an automatic cancel."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.auth.vault_unlock_timings import (
    OWNER_CERTIFICATE_WAIT,
    OWNER_WAIT_REMINDER_EVERY,
    OWNER_WAIT_REMINDER_OFFSETS,
)
from app.database import users_collection

ALERT_KIND = "death_certificate_wait"


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def reminder_day_numbers() -> tuple[int, ...]:
    return tuple(int(offset.total_seconds() // 86400) for offset in OWNER_WAIT_REMINDER_OFFSETS)


def public_owner_wait(owner: dict | None) -> dict | None:
    owner = owner or {}
    started = _as_utc(owner.get("owner_wait_started_at"))
    if not started:
        alert = owner.get("death_claim_alert")
        return alert if isinstance(alert, dict) else None
    ends = _as_utc(owner.get("owner_wait_ends_at")) or (started + OWNER_CERTIFICATE_WAIT)
    now = datetime.now(timezone.utc)
    elapsed = bool(owner.get("owner_wait_elapsed")) or now >= ends
    remaining_seconds = max(0, int((ends - now).total_seconds())) if not elapsed else 0
    remaining = remaining_seconds // 86400
    reporter = str(owner.get("owner_wait_reporter_name") or "Someone you named")
    title = "Security alert: after-death access request"
    if elapsed:
        body = (
            "The 7-day (168-hour) protection period has ended. Nothing in your Vault "
            "has been released. An admin must still review the request. If you are "
            "alive, choose I Am Alive — Stop Request."
        )
    else:
        body = (
            "Someone reported that you have passed and started an after-death access "
            "request. Nothing has been released. Your Vault remains sealed for a "
            f"mandatory 7-day protection period. About {remaining} day"
            f"{'' if remaining == 1 else 's'} remain. Choose I Am Alive — Stop Request "
            "if this is false."
        )
    return {
        "kind": ALERT_KIND,
        "title": title,
        "body": body,
        "started_at": started,
        "ends_at": ends,
        "elapsed": elapsed,
        "remaining_days": remaining,
        "remaining_seconds": remaining_seconds,
        "can_dispute": True,
        "wait_days": 7,
        "reminder_every_days": 2,
        "reporter_name": reporter,
    }


async def start_certificate_wait(*, owner: dict, reporter_name: str) -> bool:
    """Start the 7-day owner window once, when the certificate first lands."""
    if owner.get("owner_wait_started_at"):
        return False
    if owner.get("owner_status") == "deceased":
        return False

    now = datetime.now(timezone.utc)
    ends = now + OWNER_CERTIFICATE_WAIT
    who = (reporter_name or "Someone you named").strip()
    owner["owner_wait_started_at"] = now
    owner["owner_wait_ends_at"] = ends
    owner["owner_wait_reporter_name"] = who
    owner["owner_wait_reminders_sent"] = []
    owner["owner_wait_elapsed"] = False
    alert = public_owner_wait(owner)
    result = await users_collection.update_one(
        {
            "_id": owner["_id"],
            "owner_wait_started_at": {"$exists": False},
            "owner_status": {"$ne": "deceased"},
        },
        {
            "$set": {
                "owner_wait_started_at": now,
                "owner_wait_ends_at": ends,
                "owner_wait_reporter_name": who,
                "owner_wait_reminders_sent": [],
                "owner_wait_elapsed": False,
                "death_claim_alert": alert,
                "updated_at": now,
            }
        },
    )
    if getattr(result, "modified_count", 0) != 1 and getattr(result, "matched_count", 0) != 1:
        return False
    owner["death_claim_alert"] = alert
    try:
        from app.notifications.owner_nok_alerts import notify_owner_certificate_wait

        await notify_owner_certificate_wait(
            owner=owner,
            reporter_name=who,
            wait_ends_at=ends,
            reminder=False,
            remaining_days=7,
        )
    except Exception as exc:
        print("⚠️ Owner certificate-wait notice failed:", exc)
    return True


def cancel_wait_fields(now: datetime) -> dict:
    return {
        "owner_wait_started_at": None,
        "owner_wait_ends_at": None,
        "owner_wait_reporter_name": None,
        "owner_wait_reminders_sent": [],
        "owner_wait_elapsed": False,
        "death_claim_alert": None,
        "owner_wait_cancelled_at": now,
    }


def wait_blocks_release(owner: dict | None, *, wait_override: bool) -> bool:
    if wait_override:
        return False
    info = public_owner_wait(owner)
    if not info:
        return False
    return not bool(info.get("elapsed"))


async def process_owner_certificate_wait() -> dict[str, int]:
    """Send day-2/4/6 reminders and mark the window elapsed after 7 days."""
    now = datetime.now(timezone.utc)
    reminded = 0
    closed = 0
    cursor = users_collection.find(
        {
            "role": "owner",
            "owner_wait_started_at": {"$exists": True, "$ne": None},
            "owner_status": {"$ne": "deceased"},
            "death_report_pending": True,
        }
    )
    async for owner in cursor:
        started = _as_utc(owner.get("owner_wait_started_at"))
        ends = _as_utc(owner.get("owner_wait_ends_at"))
        if not started:
            continue
        if not ends:
            ends = started + OWNER_CERTIFICATE_WAIT

        if now >= ends:
            if not owner.get("owner_wait_elapsed"):
                alert = public_owner_wait({**owner, "owner_wait_elapsed": True})
                await users_collection.update_one(
                    {"_id": owner["_id"]},
                    {
                        "$set": {
                            "owner_wait_elapsed": True,
                            "death_claim_alert": alert,
                            "updated_at": now,
                        }
                    },
                )
                closed += 1
            continue

        sent = {
            int(day)
            for day in (owner.get("owner_wait_reminders_sent") or [])
            if str(day).isdigit() or isinstance(day, int)
        }
        elapsed = now - started
        for offset in OWNER_WAIT_REMINDER_OFFSETS:
            day = int(offset.total_seconds() // 86400)
            if day in sent:
                continue
            if elapsed < offset:
                continue
            remaining = max(0, int((ends - now).total_seconds() // 86400))
            try:
                from app.notifications.owner_nok_alerts import (
                    notify_owner_certificate_wait,
                )

                await notify_owner_certificate_wait(
                    owner=owner,
                    reporter_name=str(owner.get("owner_wait_reporter_name") or "Someone you named"),
                    wait_ends_at=ends,
                    reminder=True,
                    remaining_days=remaining,
                    reminder_day=day,
                )
            except Exception as exc:
                print(f"⚠️ Owner wait reminder failed for {owner.get('_id')}: {exc}")
                continue
            sent.add(day)
            alert = public_owner_wait(owner)
            await users_collection.update_one(
                {"_id": owner["_id"]},
                {
                    "$set": {
                        "owner_wait_reminders_sent": sorted(sent),
                        "death_claim_alert": alert,
                        "updated_at": now,
                    }
                },
            )
            reminded += 1
    return {"reminded": reminded, "closed": closed}
