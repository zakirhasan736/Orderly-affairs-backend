"""
Universal expiry / renewal reminder scheduler.

Scans ALL kit sections for date-like expiry / deadline / maturity fields
(insurance, vehicle registration, taxes, mortgages, loans, leases, memberships,
legal document expirations, etc.) and emails owner + immediate-access people on:
  10 days → 5 days → 1 day → due / expiry day (0).

Mirrors the trial billing countdown pattern: daily cron counts days until the
stored date and fires threshold emails once each.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database import db, section_data_collection, users_collection
from app.notifications.display_names import (
    resolve_nextkin_display_name,
    resolve_owner_display_name,
)
from app.auth.access_types import resolve_access_type
from app.auth.notification_prefs import get_owner_notification_prefs
from app.notifications.expiry_reminder_emails import send_expiry_reminder_email
from app.notifications.web_push import (
    default_push_click_url,
    send_web_push_to_email,
    vapid_configured,
)
from app.security.access_control import nok_has_section_access
from app.security.section_crypto import decrypt_section_data

REMINDER_DAYS = (10, 5, 1, 0)
reminder_log = db["section_expiry_reminder_log"]

scheduler = AsyncIOScheduler()

# Match renewal / expiry / tax / loan / mortgage deadline keys.
# Do NOT match bare "renewal" alone (would hit renewal_requirements text).
EXPIRY_KEY_RE = re.compile(
    r"("
    r"expir|expiration|expiry|"
    r"renewal_date|policy_expiry|registration_expiry|"
    r"passport_expiry|license_expiry|drivers_license_expiry|"
    r"valid_through|valid_until|end_date|lease_end_date|"
    r"maturity_date|loan_maturity|mortgage_maturity|"
    r"tax_filing_deadline|property_tax_due|filing_deadline|"
    r"next_payment_due_date|next_due_date|cd_maturity|"
    r"warranty_expiry|subscription_renewal"
    r")",
    re.I,
)
SKIP_KEY_RE = re.compile(
    r"(requirement|instruction|header|note|location|document|upload|files)",
    re.I,
)
# Day-of-month text fields — not absolute calendar deadlines.
SKIP_EXACT_KEYS = frozenset({"payment_due_date"})

SECTION_TITLES = {
    "1": "Vital Information",
    "5": "Vehicles",
    "6": "Main Residence",
    "7": "Insurance Policies",
    "8": "Organizations & Memberships",
    "9": "Charitable Contributions",
    "10": "Education History",
    "11": "Military Service",
    "12": "Bank Accounts",
    "13": "Passwords & Online Accounts",
    "14": "Investments",
    "15": "Healthcare",
    "16": "Credit Cards & Debt",
    "17": "Family & Relationships",
    "18": "Employment & Income",
    "19": "Assets & Valuables",
    "20": "Legal Documents & Records",
    "21": "Estate Planning & Final Wishes",
}


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "label", "name", "value", "date"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return ""


def parse_expiry_date(raw: str | None) -> datetime | None:
    text = _as_text(raw)
    if not text:
        return None

    try:
        if "T" in text:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        return datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        pass

    patterns = (
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%m-%d-%Y",
        "%d-%m-%Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
    )
    for pattern in patterns:
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue

    match = re.search(r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", text)
    if match:
        return parse_expiry_date(match.group(1))

    return None


def _fingerprint(section_id: str, field_key: str, label: str, expiry_iso: str) -> str:
    raw = f"{section_id}|{field_key}|{label}|{expiry_iso}".lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _human_field_label(field_key: str) -> str:
    return field_key.replace("_", " ").strip().title()


def collect_expiry_events(section_id: str, data: dict) -> list[dict]:
    """Walk section data and return expiry events for reminder scheduling."""
    events: list[dict] = []

    def walk(node, path: list[str]):
        if isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, path + [str(index)])
            return

        if not isinstance(node, dict):
            return

        # Per-item reminder recipients (insurance policies, etc.)
        item_recipients = node.get("reminder_recipients")

        for key, value in node.items():
            key_str = str(key)
            if key_str in SKIP_EXACT_KEYS:
                if isinstance(value, (dict, list)):
                    walk(value, path + [key_str])
                continue

            if SKIP_KEY_RE.search(key_str) and not EXPIRY_KEY_RE.search(key_str):
                if isinstance(value, (dict, list)):
                    walk(value, path + [key_str])
                continue

            if EXPIRY_KEY_RE.search(key_str):
                expiry = parse_expiry_date(_as_text(value))
                if expiry:
                    # Build a short label from sibling fields when possible
                    sibling_bits = [
                        _as_text(node.get(k))
                        for k in (
                            "policy_type",
                            "policy_company",
                            "insurance_company",
                            "make",
                            "model",
                            "year",
                            "document_type",
                            "account_name",
                            "provider",
                            "creditor_name",
                            "debt_type",
                            "bank_name",
                            "organization_name",
                            "home_address",
                            "property_address",
                            "property_type",
                            "lender",
                            "card_name",
                        )
                        if _as_text(node.get(k))
                    ]
                    label_parts = sibling_bits[:2] or [_human_field_label(key_str)]
                    events.append(
                        {
                            "section_id": section_id,
                            "field_key": key_str,
                            "label": " · ".join(label_parts),
                            "expiry": expiry,
                            "reminder_recipients": item_recipients,
                            "path": ".".join(path + [key_str]),
                        }
                    )

            if isinstance(value, (dict, list)):
                walk(value, path + [key_str])

    if isinstance(data, dict):
        walk(data, [])
    return events


async def _already_sent(
    owner_id: str,
    fingerprint: str,
    days_before: int,
    expiry_iso: str,
) -> bool:
    doc = await reminder_log.find_one(
        {
            "owner_id": owner_id,
            "fingerprint": fingerprint,
            "days_before": days_before,
            "expiry_date": expiry_iso,
        }
    )
    return bool(doc)


async def _mark_sent(
    owner_id: str,
    fingerprint: str,
    days_before: int,
    expiry_iso: str,
) -> None:
    now = datetime.utcnow()
    await reminder_log.update_one(
        {
            "owner_id": owner_id,
            "fingerprint": fingerprint,
            "days_before": days_before,
            "expiry_date": expiry_iso,
        },
        {
            "$set": {"sent_at": now, "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


async def _default_recipients(owner: dict) -> list[dict]:
    """Owner + immediate-access NOK/family (with user docs for ACL + deep links)."""
    recipients: list[dict] = []
    owner_email = (owner.get("email") or "").strip().lower()
    if owner_email:
        recipients.append(
            {
                "email": owner_email,
                "name": await resolve_owner_display_name(owner),
                "role": "owner",
                "access_type": "owner",
                "user": owner,
            }
        )

    owner_id = str(owner["_id"])
    cursor = users_collection.find(
        {
            "role": "nextkin",
            "owner_id": owner_id,
            "immediate_access": True,
            "access_revoked": {"$ne": True},
        }
    )
    async for nok in cursor:
        email = (nok.get("email") or "").strip().lower()
        if not email:
            continue
        recipients.append(
            {
                "email": email,
                "name": resolve_nextkin_display_name(nok),
                "role": "nextkin",
                "access_type": resolve_access_type(nok),
                "user": nok,
            }
        )

    seen: set[str] = set()
    unique: list[dict] = []
    for item in recipients:
        if item["email"] in seen:
            continue
        seen.add(item["email"])
        unique.append(item)
    return unique


def _selected_recipients(event: dict, defaults: list[dict]) -> list[dict]:
    raw = event.get("reminder_recipients")
    if raw is None:
        return defaults

    if isinstance(raw, list):
        selected = {
            str(item).strip().lower()
            for item in raw
            if isinstance(item, str) and item.strip()
        }
        if not selected:
            return []
        return [item for item in defaults if item["email"] in selected]

    return defaults


def _recipient_can_receive_section(recipient: dict, section_id: str) -> bool:
    if recipient.get("role") == "owner":
        return True
    user = recipient.get("user")
    if not isinstance(user, dict):
        return False
    return nok_has_section_access(user, section_id)


async def process_section_expiry_reminders() -> None:
    now = datetime.utcnow()
    today = datetime(now.year, now.month, now.day)

    owners = users_collection.find({"role": "owner"})
    async for owner in owners:
        owner_id = str(owner["_id"])
        prefs = get_owner_notification_prefs(owner)
        email_enabled = bool(prefs.get("email_reminders_enabled", True))
        push_enabled = (
            vapid_configured() and str(prefs.get("push_state") or "") == "active"
        )
        push_for_collaborators = bool(prefs.get("push_for_collaborators", True))
        if not email_enabled and not push_enabled:
            continue

        defaults = await _default_recipients(owner)
        owner_name = await resolve_owner_display_name(owner)

        cursor = section_data_collection.find(
            {"owner_id": owner_id},
            {"section_id": 1, "encrypted_data": 1},
        )
        async for section in cursor:
            section_id = str(section.get("section_id") or "")
            encrypted = section.get("encrypted_data")
            if not section_id or not encrypted:
                continue

            try:
                data = decrypt_section_data(owner_id, section_id, encrypted)
            except Exception as exc:
                print(f"expiry scan decrypt failed {owner_id}/{section_id}: {exc}")
                continue

            if not isinstance(data, dict):
                continue

            for event in collect_expiry_events(section_id, data):
                expiry = event["expiry"]
                expiry_day = datetime(expiry.year, expiry.month, expiry.day)
                days_until = (expiry_day - today).days
                if days_until not in REMINDER_DAYS:
                    continue

                expiry_iso = expiry_day.strftime("%Y-%m-%d")
                fingerprint = _fingerprint(
                    section_id,
                    event["field_key"],
                    event["label"],
                    expiry_iso,
                )

                if await _already_sent(
                    owner_id, fingerprint, days_until, expiry_iso
                ):
                    continue

                recipients = [
                    item
                    for item in _selected_recipients(event, defaults)
                    if _recipient_can_receive_section(item, section_id)
                ]
                if not recipients:
                    continue

                section_title = SECTION_TITLES.get(section_id, f"Section {section_id}")
                item_label = event["label"]
                sent_any = False
                when = (
                    "today"
                    if days_until == 0
                    else f"in {days_until} day{'s' if days_until != 1 else ''}"
                )
                push_title = f"{item_label} — due {when}"
                push_body = (
                    f"{section_title}: {item_label} expires {expiry_iso}."
                )
                push_tag = f"expiry-{fingerprint}-{days_until}"

                for recipient in recipients:
                    is_owner = recipient.get("role") == "owner"
                    if email_enabled:
                        try:
                            send_expiry_reminder_email(
                                to_email=recipient["email"],
                                recipient_name=recipient.get("name") or "",
                                owner_name=owner_name,
                                section_title=section_title,
                                item_label=item_label,
                                field_label=_human_field_label(event["field_key"]),
                                expiry_date=expiry_iso,
                                days_before=days_until,
                            )
                            sent_any = True
                        except Exception as exc:
                            print(
                                "expiry reminder email failed "
                                f"to={recipient.get('email')} owner={owner_id}: {exc}"
                            )

                    recipient_push = push_enabled and (
                        is_owner or push_for_collaborators
                    )
                    if recipient_push:
                        push_url = default_push_click_url(recipient.get("user"))
                        try:
                            pushed = await send_web_push_to_email(
                                recipient["email"],
                                title=push_title,
                                body=push_body,
                                url=push_url,
                                tag=push_tag,
                                owner_id=owner_id,
                            )
                            if pushed:
                                sent_any = True
                        except Exception as exc:
                            print(
                                "expiry reminder push failed "
                                f"to={recipient.get('email')} owner={owner_id}: {exc}"
                            )

                if sent_any:
                    await _mark_sent(
                        owner_id, fingerprint, days_until, expiry_iso
                    )


def start_section_expiry_scheduler() -> None:
    scheduler.add_job(
        process_section_expiry_reminders,
        trigger="cron",
        hour=8,
        minute=5,
        id="section-expiry-reminder-job",
        replace_existing=True,
    )
    if not scheduler.running:
        scheduler.start()


# Back-compat alias used by older imports
start_insurance_expiry_scheduler = start_section_expiry_scheduler
process_insurance_expiry_reminders = process_section_expiry_reminders
