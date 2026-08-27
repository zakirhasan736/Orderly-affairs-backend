"""Domain events for after-death owner protection. One event, then channel fan-out."""

from __future__ import annotations

from datetime import datetime, timezone

from app.auth.after_death_case import (
    maybe_mark_eligible,
    enrolled_claimants,
    record_notice,
    write_audit,
    cases_collection,
)
from app.auth.after_death_policy import (
    ADMIN_RELEASE_SLA,
    as_utc,
    reminder_slot,
)
from app.config import settings
from app.database import users_collection


EVENT_INITIAL = "DEATH_ACCESS_INITIAL_NOTICE"
EVENT_DAY2 = "DEATH_ACCESS_DAY_2_REMINDER"
EVENT_DAY4 = "DEATH_ACCESS_DAY_4_REMINDER"
EVENT_DAY6 = "DEATH_ACCESS_DAY_6_FINAL"
EVENT_COMPLETED = "DEATH_ACCESS_PERIOD_COMPLETED"
EVENT_DISPUTED = "DEATH_ACCESS_OWNER_DISPUTED"
EVENT_ELIGIBLE = "DEATH_ACCESS_ELIGIBLE_FOR_ADMIN"
EVENT_ADMIN_REALERT = "DEATH_ACCESS_ADMIN_REALERT"


def _copy(event: str) -> tuple[str, str, str]:
    """email title, email body, lock-screen push (minimal)."""
    push = "Important account security alert. An access request requires your attention."
    stop = "If you are alive, sign in and choose I Am Alive — Stop Request. Nothing in your Vault has been released."
    if event == EVENT_INITIAL:
        return (
            "Security alert: after-death access request",
            "A death certificate was stored for an after-death access request. "
            "Nothing has been released. Your Vault remains sealed. A mandatory 7-day "
            f"(168-hour) protection period has started. {stop}",
            push,
        )
    if event == EVENT_DAY2:
        return (
            "Reminder: about five days remain",
            "This is a scheduled reminder. About five days remain in the 7-day protection "
            f"period. No information has been released. {stop}",
            push,
        )
    if event == EVENT_DAY4:
        return (
            "Reminder: about three days remain",
            "This is a scheduled reminder. About three days remain in the 7-day protection "
            f"period. No information has been released. {stop}",
            push,
        )
    if event == EVENT_DAY6:
        return (
            "FINAL security reminder: about one day remains",
            "FINAL SECURITY REMINDER. About one day remains in the protection period. "
            f"No information has been released. {stop}",
            push,
        )
    if event == EVENT_COMPLETED:
        return (
            "Protection period complete — vault still sealed",
            "The 7-day protection period has ended. Your Vault is still sealed. "
            "An Orderly Affairs admin must still review and manually release access. "
            f"{stop}",
            push,
        )
    if event == EVENT_DISPUTED:
        return (
            "After-death request stopped",
            "You confirmed you are alive. The after-death access request is frozen. "
            "Nothing was released.",
            push,
        )
    return ("Account security alert", stop, push)


async def emit_owner_event(
    *,
    event: str,
    case: dict,
    owner: dict,
    audit_event: str,
) -> None:
    if case.get("owner_disputed") or case.get("status") in {"OWNER_DISPUTED", "REJECTED", "CLOSED"}:
        return
    title, body, push = _copy(event)
    owner_id = str(owner.get("_id") or "")
    channels = ("EMAIL", "PUSH", "IN_APP")
    any_new = False
    for channel in channels:
        is_new = await record_notice(
            case=case,
            user_id=owner_id,
            event_type=event,
            channel=channel,
            title=title,
            message=body if channel != "PUSH" else push,
            status="queued",
        )
        if not is_new:
            continue
        any_new = True
        sent = True
        failure = None
        try:
            if channel == "EMAIL":
                await _send_email(owner, title=title, body=body)
            elif channel == "PUSH":
                await _send_push(owner, title=push, body=push)
            elif channel == "IN_APP":
                await _set_in_app(owner, case=case, title=title, body=body)
        except Exception as exc:
            sent = False
            failure = str(exc)[:240]
            print(f"⚠️ after-death {event} {channel} failed: {exc}")
        from app.auth.after_death_case import notice_collection

        await notice_collection.update_one(
            {"idempotency_key": f"death-case:{case['_id']}:{event}:{channel}"},
            {
                "$set": {
                    "status": "sent" if sent else "failed",
                    "sent_at": datetime.now(timezone.utc) if sent else None,
                    "failure_reason": failure,
                }
            },
        )
    if any_new:
        await write_audit(event=audit_event, case=case, actor_type="system")


async def _send_email(owner: dict, *, title: str, body: str) -> None:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    from app.notifications.email_layout import email_cta_row, kit_url, paper_body, render_reminder_card

    html = render_reminder_card(
        schedule_label="After-death protection",
        title=title,
        preheader="Your Vault is still sealed.",
        warning=True,
        body_html="".join(
            [
                paper_body(body),
                email_cta_row((kit_url(), "I Am Alive — Stop Request")),
            ]
        ),
    )
    sg = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
    sg.send(
        Mail(
            from_email=settings.EMAIL_SENDER,
            to_emails=owner["email"],
            subject=f"Orderly Affairs – {title}",
            html_content=html,
        )
    )


async def _send_push(owner: dict, *, title: str, body: str) -> None:
    from app.notifications.email_layout import kit_url
    from app.notifications.push_bridge import notify_web_push

    await notify_web_push(
        owner,
        title=title,
        body=body,
        tag="after-death-security",
        url=kit_url(),
        urgency="high",
    )


async def _set_in_app(owner: dict, *, case: dict, title: str, body: str) -> None:
    from app.auth.owner_wait import public_owner_wait

    now = datetime.now(timezone.utc)
    owner["owner_wait_started_at"] = case.get("owner_notice_started_at")
    owner["owner_wait_ends_at"] = case.get("owner_notice_expires_at")
    owner["owner_wait_reporter_name"] = case.get("reporter_relationship") or "Someone you named"
    alert = public_owner_wait(owner) or {}
    alert["title"] = title
    alert["body"] = body
    await users_collection.update_one(
        {"_id": owner["_id"]},
        {
            "$set": {
                "death_claim_alert": alert,
                "owner_wait_started_at": case.get("owner_notice_started_at"),
                "owner_wait_ends_at": case.get("owner_notice_expires_at"),
                "updated_at": now,
            }
        },
    )


async def send_initial_if_needed(case: dict, owner: dict) -> None:
    if case.get("initial_notice_sent_at"):
        return
    await emit_owner_event(
        event=EVENT_INITIAL,
        case=case,
        owner=owner,
        audit_event="OWNER_INITIAL_NOTICE_SENT",
    )
    now = datetime.now(timezone.utc)
    await cases_collection.update_one(
        {"_id": case["_id"], "initial_notice_sent_at": None},
        {"$set": {"initial_notice_sent_at": now}},
    )
    case["initial_notice_sent_at"] = now


async def process_after_death_clocks() -> dict[str, int]:
    now = datetime.now(timezone.utc)
    reminded = 0
    closed = 0
    eligible_alerts = 0
    realerts = 0
    cursor = cases_collection.find(
        {
            "owner_disputed": {"$ne": True},
            "status": {"$nin": ["OWNER_DISPUTED", "REJECTED", "CLOSED"]},
        }
    )
    async for case in cursor:
        try:
            from bson import ObjectId

            owner = await users_collection.find_one(
                {"_id": ObjectId(str(case["owner_id"])), "role": "owner"}
            )
        except Exception:
            owner = None
        if not owner:
            continue
        started = as_utc(case.get("owner_notice_started_at"))
        expires = as_utc(case.get("owner_notice_expires_at"))
        if not started:
            continue
        elapsed = now - started
        slot = reminder_slot(elapsed)
        field = {2: "day2_notice_sent_at", 4: "day4_notice_sent_at", 6: "day6_notice_sent_at"}.get(
            slot or 0
        )
        event = {2: EVENT_DAY2, 4: EVENT_DAY4, 6: EVENT_DAY6}.get(slot or 0)
        audit = {
            2: "OWNER_DAY_2_REMINDER_SENT",
            4: "OWNER_DAY_4_REMINDER_SENT",
            6: "OWNER_DAY_6_FINAL_SENT",
        }.get(slot or 0)
        if field and event and audit and not case.get(field):
            await emit_owner_event(event=event, case=case, owner=owner, audit_event=audit)
            await cases_collection.update_one(
                {"_id": case["_id"], field: None},
                {"$set": {field: now}},
            )
            reminded += 1

        if expires and now >= expires and not case.get("owner_notice_completed_at"):
            await cases_collection.update_one(
                {"_id": case["_id"], "owner_notice_completed_at": {"$exists": False}},
                {"$set": {"owner_notice_completed_at": now, "updated_at": now}},
            )
            await write_audit(event="OWNER_PROTECTION_PERIOD_COMPLETED", case=case, actor_type="system")
            await emit_owner_event(
                event=EVENT_COMPLETED,
                case=case,
                owner=owner,
                audit_event="OWNER_PROTECTION_PERIOD_COMPLETED",
            )
            closed += 1

        claimants = await enrolled_claimants(str(owner["_id"]))
        gates = await maybe_mark_eligible(case, claimants)
        if gates.get("eligible_for_admin_release"):
            if not case.get("admin_alerted_at"):
                await _alert_admins(case, event=EVENT_ELIGIBLE, realert=False)
                await cases_collection.update_one(
                    {"_id": case["_id"], "admin_alerted_at": {"$exists": False}},
                    {"$set": {"admin_alerted_at": now}},
                )
                case["admin_alerted_at"] = now
                eligible_alerts += 1
            elif not case.get("admin_release") and not case.get("admin_realerted_at"):
                alerted = as_utc(case.get("admin_alerted_at"))
                if alerted and now >= alerted + ADMIN_RELEASE_SLA:
                    await _alert_admins(case, event=EVENT_ADMIN_REALERT, realert=True)
                    await cases_collection.update_one(
                        {"_id": case["_id"]},
                        {"$set": {"admin_realerted_at": now, "supervisor_alerted_at": now}},
                    )
                    await write_audit(
                        event="ADMIN_RELEASE_REALERT_SENT",
                        case=case,
                        actor_type="system",
                    )
                    realerts += 1
    return {
        "reminded": reminded,
        "closed": closed,
        "eligible_alerts": eligible_alerts,
        "realerts": realerts,
    }


async def _alert_admins(case: dict, *, event: str, realert: bool) -> None:
    from app.admin.audit import log_admin_action
    from app.database import admin_security_alerts_collection

    now = datetime.now(timezone.utc)
    try:
        await admin_security_alerts_collection.insert_one(
            {
                "kind": event,
                "case_id": str(case["_id"]),
                "owner_id": case.get("owner_id"),
                "reference": case.get("reference"),
                "realert": realert,
                "created_at": now,
            }
        )
    except Exception as exc:
        print("⚠️ admin security alert insert failed:", exc)
    await log_admin_action(
        "system",
        "nok.after_death_eligible" if not realert else "nok.after_death_realert",
        case.get("reference"),
        {"case_id": str(case["_id"]), "realert": realert},
    )
    if not realert:
        await write_audit(event="ADMIN_RELEASE_ALERT_SENT", case=case, actor_type="system")
