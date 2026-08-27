"""Durable AfterDeathAccessCase. Living NOK release never writes here."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from app.auth.after_death_policy import (
    MANUAL_REVIEW_SLA,
    derive_case_status,
    didit_needs_manual_review,
    normalize_didit,
    normalize_ssdmf,
    protection_expires_at,
    release_gates,
    ssdmf_needs_manual_review,
)
from app.database import db
from app.auth.access_types import ACCESS_TYPE_FAMILY, NEXTKIN_ACCESS_MONGO_FILTER, resolve_access_type
from app.auth.claimant_roles import is_attorney_or_executor

cases_collection = db["after_death_access_cases"]
certs_collection = db["after_death_certificates"]
audit_collection = db["after_death_audit_events"]
notice_collection = db["after_death_notifications"]


def _oid(value) -> ObjectId | None:
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError, ValueError):
        return None


def case_ref(case: dict) -> str:
    return str(case.get("reference") or case.get("_id") or "")


async def write_audit(
    *,
    event: str,
    case: dict | None = None,
    actor_type: str = "system",
    actor_id: str | None = None,
    metadata: dict | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    doc = {
        "event": event,
        "case_id": str(case["_id"]) if case and case.get("_id") else None,
        "owner_id": str((case or {}).get("owner_id") or ""),
        "actor_type": actor_type,
        "actor_id": actor_id,
        "timestamp": now,
        "metadata": metadata or {},
        "ip_address": ip,
        "user_agent": user_agent,
    }
    try:
        await audit_collection.insert_one(doc)
    except Exception as exc:
        print(f"⚠️ after-death audit failed ({event}): {exc}")


async def record_notice(
    *,
    case: dict,
    user_id: str,
    event_type: str,
    channel: str,
    title: str,
    message: str,
    status: str,
    failure_reason: str | None = None,
) -> bool:
    """Insert once per case+event+channel. Returns True if this send is new."""
    key = f"death-case:{case['_id']}:{event_type}:{channel}"
    now = datetime.now(timezone.utc)
    result = await notice_collection.update_one(
        {"idempotency_key": key},
        {
            "$setOnInsert": {
                "idempotency_key": key,
                "case_id": str(case["_id"]),
                "user_id": user_id,
                "event_type": event_type,
                "channel": channel,
                "title": title,
                "message": message,
                "status": status,
                "scheduled_at": now,
                "sent_at": now if status == "sent" else None,
                "failure_reason": failure_reason,
                "created_at": now,
            }
        },
        upsert=True,
    )
    return bool(getattr(result, "upserted_id", None))


async def open_case_for_owner(owner_id: str) -> dict | None:
    return await cases_collection.find_one(
        {
            "owner_id": str(owner_id),
            "owner_disputed": {"$ne": True},
            "status": {"$nin": ["OWNER_DISPUTED", "REJECTED", "CLOSED"]},
        }
    )


async def case_by_id(case_id: str) -> dict | None:
    oid = _oid(case_id)
    if not oid:
        return None
    return await cases_collection.find_one({"_id": oid})


async def enrolled_claimants(owner_id: str) -> list[dict]:
    cursor = users_collection_find(owner_id)
    out: list[dict] = []
    async for nk in cursor:
        if resolve_access_type(nk) == ACCESS_TYPE_FAMILY:
            continue
        if nk.get("access_revoked"):
            continue
        out.append(nk)
    return out


def users_collection_find(owner_id: str):
    from app.database import users_collection

    return users_collection.find(
        {
            "role": "nextkin",
            "owner_id": str(owner_id),
            "access_revoked": {"$ne": True},
            "$and": [NEXTKIN_ACCESS_MONGO_FILTER],
        }
    )


def reporter_type(user: dict) -> str:
    rel = str(user.get("relationship") or "").lower()
    if is_attorney_or_executor(user):
        if "executor" in rel:
            return "EXECUTOR"
        if "trustee" in rel:
            return "TRUSTEE"
        if "personal representative" in rel:
            return "PERSONAL_REPRESENTATIVE"
        if "estate administrator" in rel or "administrator" in rel:
            return "ESTATE_ADMINISTRATOR"
        return "ATTORNEY"
    return "NEXT_OF_KIN"


async def create_or_get_case(
    *,
    owner: dict,
    reporter: dict,
    source: str,
) -> dict:
    owner_id = str(owner["_id"])
    existing = await open_case_for_owner(owner_id)
    if existing:
        await write_audit(
            event="PASSING_REPORTED",
            case=existing,
            actor_type="claimant",
            actor_id=str(reporter.get("_id")),
            metadata={"duplicate": True, "source": source},
        )
        return existing

    now = datetime.now(timezone.utc)
    total = await cases_collection.count_documents({"owner_id": owner_id})
    reference = f"ADA-{str(owner_id)[-6:].upper()}-{total + 1:03d}"
    rtype = reporter_type(reporter)
    doc: dict[str, Any] = {
        "reference": reference,
        "owner_id": owner_id,
        "reported_by_user_id": str(reporter.get("_id")),
        "reporter_type": rtype,
        "reporter_relationship": reporter.get("relationship"),
        "reported_at": now,
        "status": "AWAITING_CERTIFICATE",
        "source": source,
        "certificate_id": None,
        "certificate_status": "NOT_UPLOADED",
        "owner_death_check_provider": "didit",
        "owner_death_check_service": "usa_states_death_check",
        "owner_death_check_status": "PENDING",
        "death_check_override": False,
        "owner_notice_started_at": None,
        "owner_notice_expires_at": None,
        "initial_notice_sent_at": None,
        "day2_notice_sent_at": None,
        "day4_notice_sent_at": None,
        "day6_notice_sent_at": None,
        "owner_disputed": False,
        "manual_review_required": False,
        "admin_release": False,
        "created_at": now,
        "updated_at": now,
    }
    result = await cases_collection.insert_one(doc)
    doc["_id"] = result.inserted_id
    await write_audit(
        event="AFTER_DEATH_CASE_CREATED",
        case=doc,
        actor_type="claimant",
        actor_id=str(reporter.get("_id")),
        metadata={"source": source, "reporter_type": rtype},
    )
    await write_audit(
        event="PASSING_REPORTED",
        case=doc,
        actor_type="claimant",
        actor_id=str(reporter.get("_id")),
        metadata={"source": source},
    )
    return doc


async def start_owner_protection_if_needed(*, case: dict, owner: dict) -> dict:
    """Start the 168-hour hold once, when the death certificate is first stored."""
    if case.get("owner_notice_started_at") and case.get("owner_notice_expires_at"):
        return case
    now = datetime.now(timezone.utc)
    expires = protection_expires_at(now)
    result = await cases_collection.update_one(
        {
            "_id": case["_id"],
            "$or": [
                {"owner_notice_started_at": {"$exists": False}},
                {"owner_notice_started_at": None},
            ],
        },
        {
            "$set": {
                "owner_notice_started_at": now,
                "owner_notice_expires_at": expires,
                "status": "PROTECTION_PERIOD_ACTIVE",
                "updated_at": now,
            }
        },
    )
    if getattr(result, "modified_count", 0) != 1:
        refreshed = await cases_collection.find_one({"_id": case["_id"]})
        return refreshed or case
    case["owner_notice_started_at"] = now
    case["owner_notice_expires_at"] = expires
    case["status"] = "PROTECTION_PERIOD_ACTIVE"
    await sync_owner_protection_clock(owner, case)
    await write_audit(event="OWNER_NOTICE_STARTED", case=case, actor_type="system")
    try:
        from app.auth.after_death_notify import send_initial_if_needed

        await send_initial_if_needed(case, owner)
    except Exception as exc:
        print("⚠️ After-death Day 0 notices failed:", exc)
    return case


async def refresh_case_status(case: dict) -> dict:
    status = derive_case_status(case)
    if case.get("status") != status:
        now = datetime.now(timezone.utc)
        await cases_collection.update_one(
            {"_id": case["_id"]},
            {"$set": {"status": status, "updated_at": now}},
        )
        case["status"] = status
        case["updated_at"] = now
    return case


async def mark_manual_review(case: dict, *, reason: str) -> None:
    if case.get("manual_review_required") and not case.get("manual_review_resolved_at"):
        return
    now = datetime.now(timezone.utc)
    due = now + MANUAL_REVIEW_SLA
    await cases_collection.update_one(
        {"_id": case["_id"]},
        {
            "$set": {
                "manual_review_required": True,
                "manual_review_reason": reason,
                "manual_review_started_at": now,
                "manual_review_due_at": due,
                "status": "MANUAL_REVIEW",
                "updated_at": now,
            }
        },
    )
    case["manual_review_required"] = True
    await write_audit(
        event="MANUAL_REVIEW_CREATED",
        case=case,
        actor_type="system",
        metadata={"reason": reason},
    )


async def store_certificate_version(
    *,
    case: dict,
    owner: dict,
    uploader: dict,
    uploaded: dict,
    contents: bytes,
    replacement_reason: str | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    current = await certs_collection.find_one(
        {"case_id": str(case["_id"]), "is_current": True}
    )
    version = int((current or {}).get("version") or 0) + 1
    if current:
        await certs_collection.update_one(
            {"_id": current["_id"]},
            {"$set": {"is_current": False, "replaced_at": now}},
        )
        await write_audit(
            event="DEATH_CERTIFICATE_REPLACED",
            case=case,
            actor_type="claimant",
            actor_id=str(uploader.get("_id")),
            metadata={"from_version": current.get("version"), "to_version": version},
        )
    digest = hashlib.sha256(contents).hexdigest()
    rec = {
        "case_id": str(case["_id"]),
        "owner_id": str(owner["_id"]),
        "version": version,
        "uploaded_by": str(uploader.get("_id")),
        "uploaded_by_name": uploader.get("full_name") or uploader.get("email"),
        "uploaded_at": now,
        "storage_key": uploaded.get("s3_key") or uploaded.get("public_id"),
        "s3_bucket": uploaded.get("s3_bucket"),
        "mime_type": uploaded.get("mime_type"),
        "size": uploaded.get("size") or len(contents),
        "filename": uploaded.get("name"),
        "content_hash": digest,
        "replacement_reason": replacement_reason,
        "is_current": True,
    }
    result = await certs_collection.insert_one(rec)
    rec["_id"] = result.inserted_id
    await cases_collection.update_one(
        {"_id": case["_id"]},
        {
            "$set": {
                "certificate_id": str(rec["_id"]),
                "certificate_status": "RECEIVED",
                "certificate_uploaded_at": now,
                "certificate_uploaded_by": str(uploader.get("_id")),
                "updated_at": now,
            }
        },
    )
    case["certificate_id"] = str(rec["_id"])
    case["certificate_uploaded_at"] = now
    await write_audit(
        event="DEATH_CERTIFICATE_UPLOADED",
        case=case,
        actor_type="claimant",
        actor_id=str(uploader.get("_id")),
        metadata={"version": version, "size": rec["size"], "content_hash": digest},
    )
    return rec


async def apply_ssdmf_to_case(case: dict, *, status: str, vendor_id: Any = None) -> None:
    now = datetime.now(timezone.utc)
    normalized = normalize_ssdmf(status)
    event = {
        "MATCH": "OWNER_DEATH_CHECK_MATCH",
        "NO_MATCH": "OWNER_DEATH_CHECK_NO_MATCH",
        "INCONCLUSIVE": "OWNER_DEATH_CHECK_INCONCLUSIVE",
        "ERROR": "OWNER_DEATH_CHECK_ERROR",
        "PENDING": "OWNER_DEATH_CHECK_STARTED",
    }.get(normalized, "OWNER_DEATH_CHECK_INCONCLUSIVE")
    await cases_collection.update_one(
        {"_id": case["_id"]},
        {
            "$set": {
                "owner_death_check_status": normalized,
                "owner_death_check_checked_at": now,
                "owner_death_check_request_ref": vendor_id,
                "updated_at": now,
            }
        },
    )
    case["owner_death_check_status"] = normalized
    await write_audit(event=event, case=case, actor_type="system", metadata={"status": normalized})
    if ssdmf_needs_manual_review(normalized):
        await mark_manual_review(
            case,
            reason=f"Owner death-record check is {normalized} (not proof the owner is alive).",
        )


async def apply_claimant_didit(case: dict | None, nextkin: dict, status: str) -> None:
    legal = is_attorney_or_executor(nextkin)
    prefix = "ATTORNEY_KYC" if legal else "NOK_KYC"
    normalized = normalize_didit(status)
    if not case:
        if legal and didit_needs_manual_review(status):
            from app.database import users_collection

            await users_collection.update_one(
                {"_id": nextkin["_id"]},
                {
                    "$set": {
                        "didit_manual_review_required": True,
                        "didit_manual_review_reason": f"Attorney/executor identity is {normalized}.",
                    }
                },
            )
            await write_audit(
                event=f"{prefix}_STATUS_CHANGED",
                case={"owner_id": str(nextkin.get("owner_id") or "")},
                actor_type="claimant",
                actor_id=str(nextkin.get("_id")),
                metadata={
                    "didit": normalized,
                    "manual_review": True,
                    "no_case_yet": True,
                },
            )
        return
    await write_audit(
        event=f"{prefix}_STATUS_CHANGED",
        case=case,
        actor_type="claimant",
        actor_id=str(nextkin.get("_id")),
        metadata={"didit": normalized},
    )
    if didit_needs_manual_review(status):
        await mark_manual_review(
            case,
            reason=f"Claimant identity is {normalized}.",
        )


async def dispute_case(
    *,
    case: dict,
    owner: dict,
    method: str,
    ip: str | None = None,
) -> dict:
    from app.database import users_collection

    now = datetime.now(timezone.utc)
    result = await cases_collection.update_one(
        {
            "_id": case["_id"],
            "owner_disputed": {"$ne": True},
        },
        {
            "$set": {
                "owner_disputed": True,
                "owner_disputed_at": now,
                "owner_dispute_method": method,
                "owner_dispute_authenticated_at": now,
                "status": "OWNER_DISPUTED",
                "updated_at": now,
            }
        },
    )
    if getattr(result, "modified_count", 0) != 1:
        case["owner_disputed"] = True
        return case
    case["owner_disputed"] = True
    case["status"] = "OWNER_DISPUTED"
    await users_collection.update_one(
        {"_id": owner["_id"]},
        {
            "$set": {
                "death_report_pending": False,
                "death_report_cancelled_at": now,
                "death_report_cancelled_reason": "owner_disputed",
                "updated_at": now,
            },
            "$unset": {
                "owner_wait_started_at": "",
                "owner_wait_ends_at": "",
                "death_claim_alert": "",
            },
        },
    )
    cursor = users_collection.find(
        {"role": "nextkin", "owner_id": str(owner["_id"]), "claim_token_hash": {"$exists": True, "$ne": ""}}
    )
    async for nk in cursor:
        if nk.get("claim_token_used_at"):
            continue
        await users_collection.update_one(
            {"_id": nk["_id"]},
            {
                "$set": {
                    "claim_token_invalidated_at": now,
                    "updated_at": now,
                },
                "$unset": {"claim_token_hash": "", "claim_token_expires_at": ""},
            },
        )
        await write_audit(
            event="CLAIM_INVALIDATED",
            case=case,
            actor_type="owner",
            actor_id=str(owner["_id"]),
            metadata={"nextkin_id": str(nk["_id"])},
        )
    await write_audit(
        event="OWNER_DISPUTED_DEATH_REQUEST",
        case=case,
        actor_type="owner",
        actor_id=str(owner["_id"]),
        metadata={"method": method},
        ip=ip,
    )
    await mark_manual_review(case, reason="Owner confirmed they are alive. Fraud review.")
    await cases_collection.update_one(
        {"_id": case["_id"]},
        {"$set": {"status": "OWNER_DISPUTED", "manual_review_required": True}},
    )
    return case


async def note_fresh_owner_login(owner: dict, ip: str | None = None) -> dict | None:
    case = await open_case_for_owner(str(owner["_id"]))
    if not case:
        return None
    now = datetime.now(timezone.utc)
    await cases_collection.update_one(
        {"_id": case["_id"]},
        {"$set": {"owner_fresh_login_at": now, "updated_at": now}},
    )
    await write_audit(
        event="OWNER_FRESH_LOGIN_DURING_DEATH_CASE",
        case=case,
        actor_type="owner",
        actor_id=str(owner["_id"]),
        ip=ip,
    )
    return case


async def maybe_mark_eligible(case: dict, claimants: list[dict]) -> dict:
    gates = release_gates(case=case, claimants=claimants)
    if not gates["eligible_for_admin_release"]:
        return {**gates, "case": await refresh_case_status(case)}
    now = datetime.now(timezone.utc)
    if not case.get("eligible_for_release_at"):
        await cases_collection.update_one(
            {"_id": case["_id"], "eligible_for_release_at": {"$exists": False}},
            {
                "$set": {
                    "eligible_for_release_at": now,
                    "status": "AWAITING_ADMIN_RELEASE",
                    "updated_at": now,
                }
            },
        )
        case["eligible_for_release_at"] = now
        await write_audit(event="CASE_ELIGIBLE_FOR_ADMIN_RELEASE", case=case, actor_type="system")
    await refresh_case_status(case)
    return {**gates, "case": case}


def public_claimant_snapshot(*, case: dict, owner: dict, nextkin: dict, claimants: list[dict]) -> dict:
    gates = release_gates(case=case, claimants=claimants)
    remaining = gates["protection_remaining_seconds"]
    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    cert_status = "Received" if gates["certificate_on_file"] else "Not uploaded"
    death = gates["owner_death_check_status"]
    death_label = {
        "MATCH": "Match confirmed",
        "NO_MATCH": "Under review",
        "INCONCLUSIVE": "Under review",
        "ERROR": "Under review",
        "PENDING": "Pending",
    }.get(death, "Pending")
    didit = normalize_didit(nextkin.get("didit_status"))
    identity_label = {
        "NOT_STARTED": "Not started",
        "IN_PROGRESS": "Processing",
        "IN_REVIEW": "In review",
        "APPROVED": "Approved",
        "DECLINED": "Needs review",
        "ABANDONED": "Needs review",
        "ERROR": "Needs review",
    }.get(didit, "Not started")
    admin_label = "Waiting"
    if gates["frozen"]:
        admin_label = "Stopped"
    elif gates["eligible_for_admin_release"]:
        admin_label = "Under review"
    elif not gates["certificate_on_file"] or not gates["protection_period_completed"]:
        admin_label = "Not ready"
    access = "Not released"
    if case.get("admin_release"):
        access = "Released"
        if nextkin.get("claim_token_hash"):
            access = "Claim available"
        if nextkin.get("claim_token_used_at"):
            access = "Claim redeemed"
    return {
        "case_reference": case_ref(case),
        "case_id": str(case["_id"]),
        "status": case.get("status"),
        "owner_display_name": owner.get("full_name") or owner.get("email") or "Vault owner",
        "relationship": nextkin.get("relationship"),
        "reporter_type": case.get("reporter_type"),
        "identity_status": didit,
        "identity_label": identity_label,
        "certificate_label": cert_status,
        "certificate_filename": None,
        "death_record_label": death_label,
        "protection_label": (
            "Complete"
            if gates["protection_period_completed"]
            else (
                "Starts when the death certificate is stored"
                if not gates.get("protection_started")
                else f"{days} days {hours:02d} hours remaining"
            )
        ),
        "protection_started": bool(gates.get("protection_started")),
        "protection_remaining_seconds": remaining,
        "protection_expires_at": case.get("owner_notice_expires_at"),
        "admin_label": admin_label,
        "access_label": access,
        "owner_disputed": gates["owner_disputed"],
        "eligible_for_admin_release": False,  # never implied as auto-unlock
    }


async def current_certificate(case: dict) -> dict | None:
    return await certs_collection.find_one(
        {"case_id": str(case["_id"]), "is_current": True}
    )


def admin_case_payload(case: dict, *, owner: dict, claimants: list[dict], cert: dict | None) -> dict:
    gates = release_gates(case=case, claimants=claimants)
    people = []
    for nk in claimants:
        people.append(
            {
                "id": str(nk["_id"]),
                "full_name": nk.get("full_name") or nk.get("name"),
                "email": nk.get("email"),
                "relationship": nk.get("relationship"),
                "reporter_type": reporter_type(nk),
                "didit_status": normalize_didit(nk.get("didit_status")),
                "email_verified": bool(nk.get("email_verified") or nk.get("immediate_access")),
                "phone_verified": bool(nk.get("phone_verified") or nk.get("phone_number")),
            }
        )
    remaining = gates["protection_remaining_seconds"]
    return {
        "id": str(case["_id"]),
        "reference": case_ref(case),
        "status": case.get("status"),
        "owner": {
            "id": str(owner.get("_id")),
            "email": owner.get("email"),
            "full_name": owner.get("full_name") or owner.get("name"),
            "owner_status": owner.get("owner_status") or "alive",
        },
        "reporter_type": case.get("reporter_type"),
        "reported_at": case.get("reported_at"),
        "claimants": people,
        "certificate": {
            "on_file": gates["certificate_on_file"],
            "status": case.get("certificate_status"),
            "version": (cert or {}).get("version"),
            "uploaded_at": (cert or {}).get("uploaded_at") or case.get("certificate_uploaded_at"),
            "uploaded_by": (cert or {}).get("uploaded_by_name"),
            "filename": (cert or {}).get("filename"),
        },
        "owner_death_record": {
            "provider": "Didit",
            "service": "USA SSDMF",
            "status": gates["owner_death_check_status"],
            "checked_at": case.get("owner_death_check_checked_at"),
            "override": bool(case.get("death_check_override")),
        },
        "protection": {
            "started_at": case.get("owner_notice_started_at"),
            "expires_at": case.get("owner_notice_expires_at"),
            "remaining_seconds": remaining,
            "started": bool(gates.get("protection_started")),
            "completed": gates["protection_period_completed"],
        },
        "notifications": {
            "day0": bool(case.get("initial_notice_sent_at")),
            "day2": bool(case.get("day2_notice_sent_at")),
            "day4": bool(case.get("day4_notice_sent_at")),
            "day6": bool(case.get("day6_notice_sent_at")),
        },
        "owner_response": {
            "disputed": gates["owner_disputed"],
            "disputed_at": case.get("owner_disputed_at"),
            "fresh_login_at": case.get("owner_fresh_login_at"),
            "method": case.get("owner_dispute_method"),
        },
        "manual_review": {
            "required": bool(case.get("manual_review_required")),
            "reason": case.get("manual_review_reason"),
            "started_at": case.get("manual_review_started_at"),
            "due_at": case.get("manual_review_due_at"),
            "resolved_at": case.get("manual_review_resolved_at"),
        },
        "gates": {
            "certificate_on_file": gates["certificate_on_file"],
            "claimant_kyc_approved": gates["claimant_didit_approved"],
            "ssdmf_match_or_override": gates["owner_death_check_ok"],
            "protection_complete": gates["protection_period_completed"],
            "protection_started": gates.get("protection_started"),
            "no_owner_dispute": not gates["owner_disputed"],
            "eligible_for_admin_release": gates["eligible_for_admin_release"],
            "reasons": gates["reasons"],
        },
        "admin_release": bool(case.get("admin_release")),
        "admin_release_at": case.get("admin_release_at"),
        "admin_release_by": case.get("admin_release_by"),
        "eligible_for_release_at": case.get("eligible_for_release_at"),
        "admin_alerted_at": case.get("admin_alerted_at"),
        "admin_realerted_at": case.get("admin_realerted_at"),
    }


async def sync_owner_protection_clock(owner: dict, case: dict) -> None:
    """Mirror case 168h timestamps onto the owner doc for session banners."""
    from app.database import users_collection

    now = datetime.now(timezone.utc)
    await users_collection.update_one(
        {"_id": owner["_id"]},
        {
            "$set": {
                "owner_wait_started_at": case.get("owner_notice_started_at"),
                "owner_wait_ends_at": case.get("owner_notice_expires_at"),
                "owner_wait_reporter_name": case.get("reporter_relationship")
                or "Someone you named",
                "updated_at": now,
            }
        },
    )
    owner["owner_wait_started_at"] = case.get("owner_notice_started_at")
    owner["owner_wait_ends_at"] = case.get("owner_notice_expires_at")


async def apply_death_check_override(
    *,
    case: dict,
    admin_email: str,
    admin_id: str | None,
    reason: str,
    notes: str,
) -> None:
    now = datetime.now(timezone.utc)
    await cases_collection.update_one(
        {"_id": case["_id"]},
        {
            "$set": {
                "death_check_override": True,
                "death_check_override_reason": reason,
                "death_check_override_notes": notes,
                "death_check_override_admin_id": admin_id,
                "death_check_override_at": now,
                "updated_at": now,
            }
        },
    )
    case["death_check_override"] = True
    await write_audit(
        event="OWNER_DEATH_CHECK_OVERRIDE",
        case=case,
        actor_type="admin",
        actor_id=admin_email,
        metadata={"reason": reason},
    )


async def cas_admin_release(*, case: dict, admin_email: str) -> bool:
    now = datetime.now(timezone.utc)
    result = await cases_collection.update_one(
        {
            "_id": case["_id"],
            "admin_release": {"$ne": True},
            "owner_disputed": {"$ne": True},
        },
        {
            "$set": {
                "admin_release": True,
                "admin_release_by": admin_email,
                "admin_release_at": now,
                "status": "ACCESS_RELEASED",
                "updated_at": now,
            }
        },
    )
    if getattr(result, "modified_count", 0) != 1:
        return False
    case["admin_release"] = True
    case["admin_release_at"] = now
    case["status"] = "ACCESS_RELEASED"
    await write_audit(
        event="ADMIN_RELEASE_APPROVED",
        case=case,
        actor_type="admin",
        actor_id=admin_email,
    )
    return True


async def ensure_after_death_indexes() -> None:
    await cases_collection.create_index("owner_id")
    await cases_collection.create_index([("owner_id", 1), ("status", 1)])
    await certs_collection.create_index([("case_id", 1), ("is_current", 1)])
    await audit_collection.create_index("case_id")
    await notice_collection.create_index("idempotency_key", unique=True)
