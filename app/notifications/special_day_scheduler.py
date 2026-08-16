"""Birthday, anniversary, and special-day wishes + NOK/family reminders."""

from __future__ import annotations

import calendar
import hashlib
import re
from datetime import date, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.auth.access_types import is_family_collaborator
from app.auth.notification_prefs import get_owner_notification_prefs
from app.config import nextkin_login_url, settings
from app.database import db, section_data_collection, users_collection
from app.notifications.display_names import (
    resolve_nextkin_display_name,
    resolve_owner_display_name,
)
from app.notifications.email_layout import (
    email_callout,
    escape,
    portal_url,
    render_simple_email,
)
from app.notifications.section_expiry_scheduler import parse_expiry_date
from app.security.section_crypto import decrypt_section_data

BIRTHDAY_KEY_RE = re.compile(r"(date_of_birth|\bdob\b|birth_date|birthday)", re.I)
ANNIVERSARY_KEY_RE = re.compile(
    r"(wedding_date|marriage_date|anniversary|wedding_anniversary)",
    re.I,
)
SKIP_KEY_RE = re.compile(r"(header|instruction|note|upload|files)", re.I)

COLLAB_LEAD_DAYS = 7
reminder_log = db["special_day_reminder_log"]
scheduler = AsyncIOScheduler()


def _safe_date(year: int, month: int, day: int) -> date | None:
    last = calendar.monthrange(year, month)[1]
    try:
        return date(year, month, min(day, last))
    except ValueError:
        return None


def days_until_month_day(month: int, day: int, today: date) -> int:
    this_year = _safe_date(today.year, month, day)
    if this_year is None:
        return -1
    if this_year >= today:
        return (this_year - today).days
    next_year = _safe_date(today.year + 1, month, day)
    if next_year is None:
        return -1
    return (next_year - today).days


def collect_special_days_from_vault(data: dict) -> list[dict]:
    found: list[dict] = []
    seen: set[tuple[str, int, int]] = set()

    def add(kind: str, parsed: datetime, label: str) -> None:
        stamp = (kind, parsed.month, parsed.day)
        if stamp in seen:
            return
        seen.add(stamp)
        found.append(
            {
                "kind": kind,
                "month": parsed.month,
                "day": parsed.day,
                "label": label,
                "enabled": True,
                "source": "vault",
            }
        )

    def walk(node, path: list[str]) -> None:
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, path + [str(index)])
            return
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            key_str = str(key)
            if SKIP_KEY_RE.search(key_str) and not (
                BIRTHDAY_KEY_RE.search(key_str) or ANNIVERSARY_KEY_RE.search(key_str)
            ):
                if isinstance(value, (dict, list)):
                    walk(value, path + [key_str])
                continue
            if BIRTHDAY_KEY_RE.search(key_str) or ANNIVERSARY_KEY_RE.search(key_str):
                parsed = parse_expiry_date(value if isinstance(value, str) else None)
                if parsed is None and isinstance(value, dict):
                    parsed = parse_expiry_date(
                        str(
                            value.get("text")
                            or value.get("date")
                            or value.get("value")
                            or ""
                        )
                    )
                if parsed:
                    kind = (
                        "birthday"
                        if BIRTHDAY_KEY_RE.search(key_str)
                        else "anniversary"
                    )
                    add(
                        kind,
                        parsed,
                        "Birthday" if kind == "birthday" else "Anniversary",
                    )
            if isinstance(value, (dict, list)):
                walk(value, path + [key_str])

    if isinstance(data, dict):
        walk(data, [])
    return found


def merge_special_days(pref_days: list[dict], vault_days: list[dict]) -> list[dict]:
    by_key: dict[tuple[str, int, int], dict] = {}
    for item in vault_days + pref_days:
        if not item.get("enabled", True):
            # Owner disabled this kind/date — keep the disabled row so vault
            # copies of the same day do not re-enable it.
            key = (str(item.get("kind")), int(item["month"]), int(item["day"]))
            by_key[key] = item
            continue
        key = (str(item.get("kind")), int(item["month"]), int(item["day"]))
        if key not in by_key:
            by_key[key] = item
    return [item for item in by_key.values() if item.get("enabled", True)]


def _fingerprint(kind: str, month: int, day: int, audience: str, year: int) -> str:
    raw = f"{kind}|{month:02d}-{day:02d}|{audience}|{year}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


async def _already_sent(owner_id: str, fingerprint: str) -> bool:
    doc = await reminder_log.find_one(
        {"owner_id": owner_id, "fingerprint": fingerprint}
    )
    return bool(doc)


async def _mark_sent(owner_id: str, fingerprint: str) -> None:
    now = datetime.utcnow()
    await reminder_log.update_one(
        {"owner_id": owner_id, "fingerprint": fingerprint},
        {"$set": {"sent_at": now, "updated_at": now}, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )


def _wish_copy(kind: str, label: str) -> tuple[str, str]:
    if kind == "birthday":
        return ("Happy birthday", "Wishing you a wonderful birthday from Orderly Affairs.")
    if kind == "anniversary":
        return (
            "Happy anniversary",
            "Wishing you a beautiful anniversary from Orderly Affairs.",
        )
    return (f"Happy {label.lower()}", f"Thinking of you on your {label.lower()}.")


async def _send_owner_wish(*, owner: dict, event: dict) -> None:
    email = str(owner.get("email") or "").strip()
    if not email or not settings.SENDGRID_API_KEY:
        return
    owner_name = await resolve_owner_display_name(owner)
    title, body = _wish_copy(str(event.get("kind")), str(event.get("label") or "special day"))
    html = render_simple_email(
        title=title,
        greeting_name=owner_name,
        paragraphs=[
            body,
            "Your vault is here whenever you want to add a note, a photo, or a "
            "message for the people you love.",
        ],
        callout_html=email_callout("A small wish from your Orderly Affairs vault.", tone="info"),
        cta_url=portal_url(),
        cta_label="Open your vault",
        preheader=title,
    )
    SendGridAPIClient(api_key=settings.SENDGRID_API_KEY).send(
        Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=email,
            subject=f"Orderly Affairs – {title}",
            html_content=html,
        )
    )
    try:
        from app.notifications.push_bridge import notify_web_push

        await notify_web_push(
            owner,
            title=title,
            body=body,
            tag=f"special-day-{event.get('kind')}",
            url=portal_url(),
            urgency="low",
        )
    except Exception as exc:
        print("⚠️ Special-day owner push failed:", exc)


async def _send_collaborator_reminder(
    *,
    person: dict,
    owner: dict,
    event: dict,
    days_until: int,
) -> None:
    email = str(person.get("email") or "").strip()
    if not email or not settings.SENDGRID_API_KEY:
        return
    owner_name = await resolve_owner_display_name(owner)
    nk_name = resolve_nextkin_display_name(person)
    label = str(event.get("label") or "special day")
    when = "today" if days_until == 0 else f"in {days_until} days"
    title = f"{owner_name}'s {label.lower()} is {when}"
    html = render_simple_email(
        title=title,
        greeting_name=nk_name,
        paragraphs=[
            f"<strong>{escape(owner_name)}</strong>'s {escape(label.lower())} is {when}.",
            "This is a quiet reminder so you can reach out, arrange something kind, "
            "or send a wish when the day arrives.",
        ],
        details=[("Occasion", label), ("When", when.title())],
        callout_html=email_callout(
            "A small note so you can wish them in person.",
            tone="info",
        ),
        cta_url=nextkin_login_url(),
        cta_label="Open Orderly Affairs",
        preheader=title,
    )
    SendGridAPIClient(api_key=settings.SENDGRID_API_KEY).send(
        Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=email,
            subject=f"Orderly Affairs – {title}",
            html_content=html,
        )
    )
    try:
        from app.notifications.push_bridge import notify_web_push

        await notify_web_push(
            person,
            title=title,
            body=f"A reminder so you can wish {owner_name}.",
            tag=f"special-day-{event.get('kind')}-{days_until}",
            url=portal_url(),
            urgency="low",
        )
    except Exception as exc:
        print("⚠️ Special-day collaborator push failed:", exc)


async def _collaborators(owner_id: str) -> list[dict]:
    people: list[dict] = []
    cursor = users_collection.find(
        {
            "owner_id": owner_id,
            "role": "nextkin",
            "access_revoked": {"$ne": True},
        }
    )
    async for person in cursor:
        if not str(person.get("email") or "").strip():
            continue
        if is_family_collaborator(person) or person.get("immediate_access"):
            people.append(person)
    return people


async def _vault_days_for_owner(owner_id: str) -> list[dict]:
    section = await section_data_collection.find_one(
        {"owner_id": owner_id, "section_id": "1"},
        {"encrypted_data": 1},
    )
    encrypted = (section or {}).get("encrypted_data")
    if not encrypted:
        return []
    try:
        data = decrypt_section_data(owner_id, "1", encrypted)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    return collect_special_days_from_vault(data)


async def process_special_day_wishes() -> None:
    today = datetime.utcnow().date()
    owners = users_collection.find({"role": "owner"})
    async for owner in owners:
        prefs = get_owner_notification_prefs(owner)
        if not prefs.get("special_days_enabled", True):
            continue
        if not prefs.get("email_reminders_enabled", True) and str(
            prefs.get("push_state") or ""
        ) != "active":
            continue

        owner_id = str(owner["_id"])
        events = merge_special_days(
            prefs.get("special_days") or [],
            await _vault_days_for_owner(owner_id),
        )
        if not events:
            continue

        collaborators = await _collaborators(owner_id)
        for event in events:
            month = int(event["month"])
            day = int(event["day"])
            kind = str(event.get("kind") or "custom")
            days_until = days_until_month_day(month, day, today)
            if days_until not in {0, COLLAB_LEAD_DAYS}:
                continue

            if days_until == 0:
                fp = _fingerprint(kind, month, day, "owner", today.year)
                if not await _already_sent(owner_id, fp):
                    try:
                        await _send_owner_wish(owner=owner, event=event)
                        await _mark_sent(owner_id, fp)
                    except Exception as exc:
                        print("⚠️ Special-day owner email failed:", owner.get("email"), exc)

            if not collaborators:
                continue
            audience = "collaborators"
            fp = _fingerprint(kind, month, day, f"{audience}:{days_until}", today.year)
            if await _already_sent(owner_id, fp):
                continue
            sent_any = False
            for person in collaborators:
                try:
                    await _send_collaborator_reminder(
                        person=person,
                        owner=owner,
                        event=event,
                        days_until=days_until,
                    )
                    sent_any = True
                except Exception as exc:
                    print(
                        "⚠️ Special-day collaborator email failed:",
                        person.get("email"),
                        exc,
                    )
            if sent_any:
                await _mark_sent(owner_id, fp)


def start_special_day_scheduler() -> None:
    scheduler.add_job(
        process_special_day_wishes,
        trigger="cron",
        hour=8,
        minute=20,
        id="special-day-wish-job",
        replace_existing=True,
    )
    if not scheduler.running:
        scheduler.start()
