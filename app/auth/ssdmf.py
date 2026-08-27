"""Didit USA States Death Master File check against the *owner* identity.

Runs after a death certificate is on file, still before admin Release.
NO_MATCH is not proof the owner is alive (file lag). Never query the NOK.
Never auto-unlock the vault.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from app.auth.didit import didit_configured, post_didit_json
from app.ai.semantic_field_map import as_plain_text, normalize_date_to_iso
from app.database import users_collection
from app.repositories.section_repository import SectionRepository
from app.security.section_crypto import decrypt_section_data
from app.security.section_e2ee import is_e2ee_doc

SSDMF_MATCH = "MATCH"
SSDMF_NO_MATCH = "NO_MATCH"
SSDMF_ERROR = "ERROR"
SSDMF_INCOMPLETE = "INCOMPLETE"
SSDMF_NOT_RUN = "NOT_RUN"

_SSN_DIGITS = re.compile(r"\D+")


def split_owner_name(full_name: str) -> tuple[str, str]:
    parts = str(full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def last4_from_ssn(raw: str | None) -> str:
    digits = _SSN_DIGITS.sub("", str(raw or ""))
    if len(digits) >= 4:
        return digits[-4:]
    return ""


def snapshot_death_check_identity(vital_info: dict | None, *, full_name_fallback: str = "") -> dict:
    vital = vital_info if isinstance(vital_info, dict) else {}
    legal = as_plain_text(vital.get("full_legal_name")) or full_name_fallback
    first, last = split_owner_name(legal or "")
    dob_raw = as_plain_text(vital.get("date_of_birth")) or ""
    dob = ""
    if dob_raw:
        try:
            dob = normalize_date_to_iso(dob_raw) or dob_raw[:10]
        except Exception:
            dob = dob_raw[:10] if len(dob_raw) >= 10 else dob_raw
    ssn4 = last4_from_ssn(as_plain_text(vital.get("social_security_number")))
    fields_used = ["first_name", "last_name"]
    if dob:
        fields_used.append("date_of_birth")
    if ssn4:
        fields_used.append("ssn_last4")
    return {
        "first_name": first,
        "last_name": last,
        "date_of_birth": dob or None,
        "ssn_last4": ssn4 or None,
        "fields_used": fields_used,
        "full_legal_name": (legal or "").strip() or None,
    }


def parse_ssdmf_response(body: dict | None) -> dict:
    data = body if isinstance(body, dict) else {}
    services = data.get("services") if isinstance(data.get("services"), dict) else {}
    check = (
        services.get("usa_states_death_check")
        if isinstance(services.get("usa_states_death_check"), dict)
        else {}
    )
    if not check and isinstance(data.get("usa_states_death_check"), dict):
        check = data["usa_states_death_check"]

    raw_status = str(
        check.get("status")
        or data.get("status")
        or check.get("result")
        or ""
    ).strip().upper()
    full_match = check.get("full_match")
    if full_match is None:
        full_match = data.get("full_match")

    if raw_status in {SSDMF_MATCH, "FULL_MATCH", "HIT", "FOUND"} or full_match is True:
        status = SSDMF_MATCH
        if full_match is None:
            full_match = True
    elif raw_status in {SSDMF_NO_MATCH, "NOT_FOUND", "MISS", "CLEAR"}:
        status = SSDMF_NO_MATCH
        if full_match is None:
            full_match = False
    elif raw_status in {"INCONCLUSIVE", "UNKNOWN", "AMBIGUOUS"}:
        status = "INCONCLUSIVE"
        if full_match is None:
            full_match = False
    elif raw_status:
        status = SSDMF_ERROR
    else:
        status = SSDMF_ERROR

    return {
        "status": status,
        "full_match": bool(full_match) if full_match is not None else None,
        "vendor_id": data.get("id") or data.get("request_id") or check.get("id"),
        "raw_status": raw_status or None,
    }


def public_death_verification(owner: dict | None) -> dict:
    owner = owner or {}
    rec = owner.get("death_verification") if isinstance(owner.get("death_verification"), dict) else {}
    cert = rec.get("certificate") if isinstance(rec.get("certificate"), dict) else {}
    ssdmf = rec.get("ssdmf") if isinstance(rec.get("ssdmf"), dict) else {}
    uploaded = bool(cert.get("uploaded_at") or owner.get("death_certificate_uploaded_at"))
    status = str(ssdmf.get("status") or owner.get("ssdmf_status") or SSDMF_NOT_RUN)
    from app.auth.owner_wait import public_owner_wait

    return {
        "certificate_uploaded": uploaded,
        "certificate_filename": cert.get("filename"),
        "certificate_uploaded_at": cert.get("uploaded_at")
        or owner.get("death_certificate_uploaded_at"),
        "certificate_uploaded_by": cert.get("uploaded_by_name"),
        "ssdmf_status": status,
        "ssdmf_full_match": ssdmf.get("full_match"),
        "ssdmf_checked_at": ssdmf.get("checked_at") or owner.get("ssdmf_checked_at"),
        "ssdmf_fields_used": ssdmf.get("fields_used") or [],
        "ssdmf_admin_override": bool(rec.get("ssdmf_admin_override")),
        "certificate_admin_override": bool(rec.get("certificate_admin_override")),
        "ssdmf_error": ssdmf.get("error"),
        "owner_identity_ready": bool(
            (owner.get("death_check_identity") or {}).get("first_name")
            and (owner.get("death_check_identity") or {}).get("last_name")
        ),
        "owner_wait": public_owner_wait(owner),
    }


def release_blockers(
    owner: dict | None,
    *,
    ssdmf_override: bool,
    certificate_override: bool,
    wait_override: bool = False,
) -> dict | None:
    """Human release still required. Block only when checks are incomplete without override."""
    info = public_death_verification(owner)
    from app.auth.owner_wait import wait_blocks_release

    needs_cert = not info["certificate_uploaded"] and not certificate_override
    ssdmf_ok = info["ssdmf_status"] == SSDMF_MATCH or ssdmf_override
    if info["certificate_uploaded"] and not ssdmf_ok:
        needs_ssdmf = True
    else:
        needs_ssdmf = False
    needs_wait = wait_blocks_release(owner, wait_override=wait_override)
    if not needs_cert and not needs_ssdmf and not needs_wait:
        return None
    wait = info.get("owner_wait") if isinstance(info.get("owner_wait"), dict) else {}
    if needs_wait:
        message = (
            "The 7-day owner notice window is still open. Wait until it ends "
            "(reminders go every 2 days), or confirm a wait override with a note."
        )
    elif needs_ssdmf:
        message = (
            "Upload a death certificate and wait for the owner Social Security "
            "Death Master File check (or confirm an override) before releasing "
            "access. NO_MATCH is not proof the owner is alive."
        )
    else:
        message = (
            "A death certificate is not on file. Upload one, or confirm a "
            "certificate override with a note."
        )
    return {
        "code": "death_verification_incomplete",
        "certificate_uploaded": info["certificate_uploaded"],
        "ssdmf_status": info["ssdmf_status"],
        "requires_certificate_override": needs_cert,
        "requires_ssdmf_override": needs_ssdmf,
        "requires_wait_override": needs_wait,
        "owner_wait_ends_at": wait.get("ends_at"),
        "message": message,
    }


async def persist_identity_snapshot(owner_id, vital_info: dict | None, *, full_name_fallback: str = "") -> dict:
    snap = snapshot_death_check_identity(vital_info, full_name_fallback=full_name_fallback)
    await users_collection.update_one(
        {"_id": owner_id},
        {
            "$set": {
                "death_check_identity": {
                    "first_name": snap["first_name"],
                    "last_name": snap["last_name"],
                    "date_of_birth": snap["date_of_birth"],
                    "ssn_last4": snap["ssn_last4"],
                    "updated_at": datetime.now(timezone.utc),
                }
            }
        },
    )
    return snap


async def _identity_for_owner(owner: dict) -> dict:
    snap = owner.get("death_check_identity") if isinstance(owner.get("death_check_identity"), dict) else {}
    if snap.get("first_name") and snap.get("last_name"):
        fields = ["first_name", "last_name"]
        if snap.get("date_of_birth"):
            fields.append("date_of_birth")
        if snap.get("ssn_last4"):
            fields.append("ssn_last4")
        return {**snap, "fields_used": fields}

    owner_id = str(owner["_id"])
    section = await SectionRepository.get(owner_id, "1")
    vital = {}
    if section and section.get("encrypted_data") and not is_e2ee_doc(section):
        try:
            plain = decrypt_section_data(owner_id, "1", section["encrypted_data"])
            vital = (plain or {}).get("vital_info") or {}
        except Exception:
            vital = {}
    fallback = str(owner.get("full_name") or owner.get("name") or "")
    return snapshot_death_check_identity(vital, full_name_fallback=fallback)


def _identity_hash(ident: dict) -> str:
    blob = "|".join(
        [
            str(ident.get("first_name") or "").lower(),
            str(ident.get("last_name") or "").lower(),
            str(ident.get("date_of_birth") or ""),
            str(ident.get("ssn_last4") or ""),
        ]
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def record_certificate_upload(
    *,
    owner: dict,
    nextkin: dict,
    uploaded: dict,
) -> dict:
    now = datetime.now(timezone.utc)
    cert = {
        "uploaded_at": now,
        "uploaded_by_id": str(nextkin.get("_id") or ""),
        "uploaded_by_name": nextkin.get("full_name") or nextkin.get("email"),
        "filename": uploaded.get("name"),
        "s3_key": uploaded.get("s3_key") or uploaded.get("public_id"),
        "s3_bucket": uploaded.get("s3_bucket"),
        "mime_type": uploaded.get("mime_type"),
        "size": uploaded.get("size"),
    }
    rec = owner.get("death_verification") if isinstance(owner.get("death_verification"), dict) else {}
    rec = {**rec, "certificate": cert}
    await users_collection.update_one(
        {"_id": owner["_id"]},
        {
            "$set": {
                "death_verification": rec,
                "death_certificate_uploaded_at": now,
                "updated_at": now,
            }
        },
    )
    owner["death_verification"] = rec
    owner["death_certificate_uploaded_at"] = now
    return rec


async def run_owner_ssdmf(owner: dict, *, force: bool = False) -> dict:
    """Query Didit DMF for the vault owner. Does not grant access."""
    now = datetime.now(timezone.utc)
    rec = owner.get("death_verification") if isinstance(owner.get("death_verification"), dict) else {}
    ssdmf = rec.get("ssdmf") if isinstance(rec.get("ssdmf"), dict) else {}

    ident = await _identity_for_owner(owner)
    first = str(ident.get("first_name") or "").strip()
    last = str(ident.get("last_name") or "").strip()
    if not first or not last:
        ssdmf = {
            "status": SSDMF_INCOMPLETE,
            "full_match": None,
            "checked_at": now,
            "error": "Owner first and last name are required from Section 1.",
            "fields_used": ident.get("fields_used") or [],
        }
        rec = {**rec, "ssdmf": ssdmf}
        await _persist_ssdmf(owner, rec, ssdmf, now)
        return public_death_verification({**owner, "death_verification": rec})

    ident_hash = _identity_hash(ident)
    if (
        not force
        and ssdmf.get("status") == SSDMF_MATCH
        and ssdmf.get("identity_hash") == ident_hash
    ):
        return public_death_verification(owner)

    if not didit_configured():
        ssdmf = {
            "status": SSDMF_ERROR,
            "full_match": None,
            "checked_at": now,
            "error": "Didit is not configured.",
            "fields_used": ident.get("fields_used") or [],
            "identity_hash": ident_hash,
        }
        rec = {**rec, "ssdmf": ssdmf}
        await _persist_ssdmf(owner, rec, ssdmf, now)
        return public_death_verification({**owner, "death_verification": rec})

    await _mark_owner_death_check_started(owner)

    payload: dict[str, Any] = {
        "issuing_state": "USA",
        "services": "usa_states_death_check",
        "first_name": first,
        "last_name": last,
        "vendor_data": f"owner:{owner['_id']}",
    }
    dob = ident.get("date_of_birth")
    if dob:
        payload["date_of_birth"] = str(dob)[:10]
    ssn4 = ident.get("ssn_last4")
    if ssn4:
        payload["ssn"] = str(ssn4)

    try:
        raw = post_didit_json("/v3/database-validation/", payload)
        parsed = parse_ssdmf_response(raw if isinstance(raw, dict) else {})
        ssdmf = {
            "status": parsed["status"],
            "full_match": parsed["full_match"],
            "checked_at": now,
            "vendor_id": parsed.get("vendor_id"),
            "raw_status": parsed.get("raw_status"),
            "fields_used": ident.get("fields_used") or [],
            "identity_hash": ident_hash,
            "error": None if parsed["status"] != SSDMF_ERROR else "Didit returned an unexpected death-file result.",
        }
    except Exception as exc:
        ssdmf = {
            "status": SSDMF_ERROR,
            "full_match": None,
            "checked_at": now,
            "fields_used": ident.get("fields_used") or [],
            "identity_hash": ident_hash,
            "error": str(exc)[:240],
        }

    rec = {**rec, "ssdmf": ssdmf}
    await _persist_ssdmf(owner, rec, ssdmf, now)
    return public_death_verification({**owner, "death_verification": rec})


async def _persist_ssdmf(owner: dict, rec: dict, ssdmf: dict, now: datetime) -> None:
    await users_collection.update_one(
        {"_id": owner["_id"]},
        {
            "$set": {
                "death_verification": rec,
                "ssdmf_status": ssdmf.get("status"),
                "ssdmf_checked_at": now,
                "ssdmf_full_match": ssdmf.get("full_match"),
                "updated_at": now,
            }
        },
    )
    owner["death_verification"] = rec
    owner["ssdmf_status"] = ssdmf.get("status")
    await _apply_ssdmf_result_to_case(owner, ssdmf)


async def _mark_owner_death_check_started(owner: dict) -> None:
    """Audit that the server is about to query Didit for the vault owner only."""
    try:
        from app.auth.after_death_case import apply_ssdmf_to_case, open_case_for_owner

        case = await open_case_for_owner(str(owner["_id"]))
        if not case:
            return
        current = str(case.get("owner_death_check_status") or "PENDING")
        if current in {"PENDING", "NOT_RUN", ""}:
            await apply_ssdmf_to_case(case, status="PENDING")
    except Exception as exc:
        print("⚠️ mark owner death-check started failed:", exc)


async def _apply_ssdmf_result_to_case(owner: dict, ssdmf: dict) -> None:
    try:
        from app.auth.after_death_case import apply_ssdmf_to_case, open_case_for_owner

        case = await open_case_for_owner(str(owner["_id"]))
        if case:
            await apply_ssdmf_to_case(
                case,
                status=str(ssdmf.get("status") or ""),
                vendor_id=ssdmf.get("vendor_id"),
            )
    except Exception as exc:
        print("⚠️ apply SSDMF to after-death case failed:", exc)


async def apply_admin_overrides(
    *,
    owner: dict,
    admin_email: str,
    ssdmf_override: bool,
    certificate_override: bool,
    note: str | None,
    wait_override: bool = False,
) -> None:
    if not ssdmf_override and not certificate_override and not wait_override:
        return
    now = datetime.now(timezone.utc)
    rec = owner.get("death_verification") if isinstance(owner.get("death_verification"), dict) else {}
    if ssdmf_override:
        rec["ssdmf_admin_override"] = True
        rec["ssdmf_admin_override_at"] = now
        rec["ssdmf_admin_override_by"] = admin_email
    if certificate_override:
        rec["certificate_admin_override"] = True
        rec["certificate_admin_override_at"] = now
        rec["certificate_admin_override_by"] = admin_email
    if wait_override:
        rec["wait_admin_override"] = True
        rec["wait_admin_override_at"] = now
        rec["wait_admin_override_by"] = admin_email
    rec["admin_override_note"] = (note or "").strip() or None
    await users_collection.update_one(
        {"_id": owner["_id"]},
        {"$set": {"death_verification": rec, "updated_at": now}},
    )
    owner["death_verification"] = rec
