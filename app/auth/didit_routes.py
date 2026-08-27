"""Didit webhook + next-of-kin identity session APIs."""

from __future__ import annotations

import json

from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime, timezone

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile

from app.auth.access_types import is_family_collaborator
from app.auth.claimant_roles import is_attorney_or_executor, public_claimant_flags
from app.auth.didit import (
    DIDIT_APPROVED,
    apply_didit_status,
    claims_require_didit,
    create_or_reuse_session,
    didit_configured,
    find_by_session_id,
    session_public_payload,
    verify_webhook_signature,
    webhook_secret,
)
from app.auth.ssdmf import (
    public_death_verification,
    record_certificate_upload,
    run_owner_ssdmf,
)
from app.config import settings
from app.database import users_collection
from app.security.cookie_auth import NOK_ACCESS_COOKIE
from app.security.document_guard import DocumentGuardError, guard_upload
from app.security.file_validation import validate_upload
from app.security.malware_scan import MalwareScanError
from app.security.token_resolver import decode_access_token
from app.security.vault_principals import require_nok_principal
from app.storage.section_s3 import upload_section_bytes_to_s3
from app.storage.vault import vault_quota_check

didit_webhook_router = APIRouter(tags=["didit"])
didit_nok_router = APIRouter(prefix="/auth", tags=["didit"])


@didit_webhook_router.post("/webhooks/didit")
async def didit_webhook(request: Request):
    secret = webhook_secret()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Didit webhook secret is not configured",
        )
    raw = await request.body()
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    if not verify_webhook_signature(
        body=body,
        signature_v2=request.headers.get("x-signature-v2"),
        signature_simple=request.headers.get("x-signature-simple"),
        signature_raw=request.headers.get("x-signature"),
        timestamp_header=request.headers.get("x-timestamp"),
        raw_body=raw,
        secret=secret,
    ):
        raise HTTPException(status_code=401, detail="Invalid signature")

    webhook_type = str(body.get("webhook_type") or "")
    if webhook_type not in {"status.updated", "data.updated"}:
        return {"ok": True, "ignored": webhook_type}

    await apply_didit_status(
        session_id=body.get("session_id"),
        vendor_data=body.get("vendor_data"),
        status=str(body.get("status") or ""),
        event_id=body.get("event_id"),
        decision=body.get("decision") if isinstance(body.get("decision"), dict) else None,
    )
    return {"ok": True}


async def _require_nok(
    request: Request,
    authorization: str | None,
    *,
    living_access: bool = True,
) -> tuple[dict, dict]:
    decoded = decode_access_token(
        request,
        authorization,
        access_cookie=NOK_ACCESS_COOKIE,
    )
    if decoded.get("role") != "nextkin":
        raise HTTPException(status_code=403, detail="Only next-of-kin can access")
    try:
        nextkin = await users_collection.find_one(
            {"_id": ObjectId(decoded["sub"]), "role": "nextkin"}
        )
    except (InvalidId, KeyError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid token") from None
    if not nextkin:
        raise HTTPException(status_code=404, detail="Next-of-Kin not found")
    require_nok_principal(nextkin)
    if is_family_collaborator(nextkin):
        raise HTTPException(status_code=403, detail="Family collaborators cannot start this process")
    if nextkin.get("access_revoked"):
        raise HTTPException(status_code=403, detail="Access not approved")
    if living_access and not nextkin.get("immediate_access"):
        raise HTTPException(status_code=403, detail="Access not approved")
    owner = None
    try:
        owner = await users_collection.find_one(
            {"_id": ObjectId(str(nextkin.get("owner_id"))), "role": "owner"}
        )
    except (InvalidId, TypeError):
        owner = None
    if not owner:
        raise HTTPException(status_code=400, detail="Owner not found")
    return nextkin, owner


@didit_nok_router.get("/nextkin/didit/status")
async def nextkin_didit_status(
    request: Request,
    authorization: str | None = Header(default=None),
):
    nextkin, owner = await _require_nok(request, authorization, living_access=False)
    pending = bool(owner.get("death_report_pending"))
    deceased = owner.get("owner_status") == "deceased"
    attorney = is_attorney_or_executor(nextkin)
    from app.auth.after_death_case import enrolled_claimants, open_case_for_owner, public_claimant_snapshot

    case = await open_case_for_owner(str(owner["_id"]))
    snapshot = None
    if case:
        snapshot = public_claimant_snapshot(
            case=case,
            owner=owner,
            nextkin=nextkin,
            claimants=await enrolled_claimants(str(owner["_id"])),
        )
    return {
        **session_public_payload(nextkin),
        **public_claimant_flags(nextkin),
        "required": pending or deceased or attorney or bool(case),
        "death_report_pending": pending,
        "owner_status": owner.get("owner_status") or "alive",
        "after_death_case": snapshot,
        "death_verification": {
            "certificate_uploaded": bool((public_death_verification(owner) or {}).get("certificate_uploaded")),
        },
    }


@didit_nok_router.post("/nextkin/didit/session")
async def nextkin_didit_session(
    request: Request,
    authorization: str | None = Header(default=None),
):
    nextkin, owner = await _require_nok(request, authorization, living_access=False)
    if not didit_configured():
        raise HTTPException(
            status_code=503,
            detail="Identity verification is not configured yet.",
        )
    try:
        session = await create_or_reuse_session(nextkin=nextkin, owner=owner)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        print(f"Didit session failed for {nextkin.get('_id')}: {exc}")
        raise HTTPException(
            status_code=502,
            detail="Could not start identity verification. Try again.",
        ) from exc
    pending = bool(owner.get("death_report_pending"))
    return {
        **session,
        **public_claimant_flags(nextkin),
        "required": True,
        "death_report_pending": pending,
        "owner_status": owner.get("owner_status") or "alive",
    }


def _didit_ok(nextkin: dict) -> bool:
    return (not claims_require_didit()) or nextkin.get("didit_status") == DIDIT_APPROVED


@didit_nok_router.get("/nextkin/death-certificate")
async def nextkin_death_certificate_status(
    request: Request,
    authorization: str | None = Header(default=None),
):
    nextkin, owner = await _require_nok(request, authorization)
    return {
        **public_claimant_flags(nextkin),
        "death_report_pending": bool(owner.get("death_report_pending")),
        "owner_status": owner.get("owner_status") or "alive",
        "certificate_uploaded": bool(
            (public_death_verification(owner) or {}).get("certificate_uploaded")
        ),
    }


@didit_nok_router.post("/nextkin/death-certificate")
async def nextkin_upload_death_certificate(
    request: Request,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    """Store a death certificate, then run SSDMF on the owner — not the claimant."""
    nextkin, owner = await _require_nok(request, authorization)
    pending = bool(owner.get("death_report_pending"))
    deceased = owner.get("owner_status") == "deceased"

    if not _didit_ok(nextkin):
        from app.auth.after_death_policy import didit_needs_manual_review

        if didit_needs_manual_review(nextkin.get("didit_status")):
            await users_collection.update_one(
                {"_id": nextkin["_id"]},
                {
                    "$set": {
                        "didit_manual_review_required": True,
                        "didit_manual_review_reason": (
                            "Identity was not Approved."
                        ),
                    }
                },
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    "Your identity check was not Approved. It is in manual review. "
                    "You cannot upload a death certificate until identity is Approved."
                ),
            )
        raise HTTPException(
            status_code=400,
            detail="Verify your identity before uploading a death certificate.",
        )
    if not pending and not deceased:
        raise HTTPException(
            status_code=400,
            detail="Report a passing before uploading a death certificate.",
        )

    try:
        validate_upload(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        guarded = guard_upload(
            contents,
            mime_type=file.content_type,
            filename=file.filename,
        )
    except (MalwareScanError, DocumentGuardError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not settings.section_s3_active:
        raise HTTPException(
            status_code=503,
            detail="File storage is not configured.",
        )

    owner_email = str(owner.get("email") or "").strip().lower()
    await vault_quota_check(
        user=owner,
        user_id=str(owner.get("_id")),
        incoming_bytes=len(guarded.payload),
        owner_email=owner_email,
    )
    try:
        uploaded = upload_section_bytes_to_s3(
            contents=guarded.payload,
            owner_email=owner_email,
            mime_type=guarded.mime_type or file.content_type or "application/octet-stream",
            original_filename=file.filename,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not store the certificate.") from exc

    if not pending and not deceased:
        from app.auth.service import record_pending_death_report

        await record_pending_death_report(
            owner=owner,
            reported_by_nextkin=nextkin,
            source="death_certificate_upload",
        )
        owner = await users_collection.find_one({"_id": owner["_id"], "role": "owner"}) or owner

    from app.auth.after_death_case import (
        create_or_get_case,
        mark_manual_review,
        open_case_for_owner,
        start_owner_protection_if_needed,
        store_certificate_version,
    )
    from app.legal.death_certificate_authorization import owner_has_death_certificate_authorization

    case = await open_case_for_owner(str(owner["_id"]))
    if not case:
        case = await create_or_get_case(
            owner=owner,
            reporter=nextkin,
            source="death_certificate_upload",
        )
    await store_certificate_version(
        case=case,
        owner=owner,
        uploader=nextkin,
        uploaded=uploaded,
        contents=guarded.payload,
    )
    await record_certificate_upload(owner=owner, nextkin=nextkin, uploaded=uploaded)
    case = await start_owner_protection_if_needed(case=case, owner=owner)

    if not owner_has_death_certificate_authorization(owner):
        await mark_manual_review(
            case,
            reason="Owner death-certificate authorization is not on file. Death-record check was not run.",
        )
        verification = public_death_verification(owner)
        return {
            "ok": True,
            "message": (
                "Death certificate received and stored privately. Owner authorization "
                "to process vital records is not on file, so the death-record check "
                "was not run. Access stays sealed."
            ),
            "after_death_case": {
                "reference": case.get("reference"),
                "certificate_status": "RECEIVED",
            },
            "death_verification": {
                "certificate_uploaded": True,
                "death_record_label": "Under review",
            },
            "uploaded_at": datetime.now(timezone.utc),
            **public_claimant_flags(nextkin),
        }

    verification = await run_owner_ssdmf(owner, force=True)
    return {
        "ok": True,
        "message": (
            "Death certificate received. We checked independent death records for the "
            "vault owner. Access stays sealed until our team releases it."
        ),
        "death_verification": {
            "certificate_uploaded": True,
            "death_record_label": (
                "Match confirmed"
                if verification.get("ssdmf_status") == "MATCH"
                else "Under review"
                if verification.get("ssdmf_status") not in {None, "", "NOT_RUN", "PENDING"}
                else "Pending"
            ),
        },
        "uploaded_at": datetime.now(timezone.utc),
        **public_claimant_flags(nextkin),
    }


@didit_nok_router.get("/nextkin/after-death-case")
async def nextkin_after_death_case(
    request: Request,
    authorization: str | None = Header(default=None),
):
    nextkin, owner = await _require_nok(request, authorization)
    from app.auth.after_death_case import enrolled_claimants, open_case_for_owner, public_claimant_snapshot

    case = await open_case_for_owner(str(owner["_id"]))
    if not case:
        return {"case": None}
    return {
        "case": public_claimant_snapshot(
            case=case,
            owner=owner,
            nextkin=nextkin,
            claimants=await enrolled_claimants(str(owner["_id"])),
        )
    }


@didit_nok_router.get("/didit/session-status")
async def public_didit_session_status(session_id: str = ""):
    """Callback landing: coarse status only, no PII."""
    nk = await find_by_session_id((session_id or "").strip())
    if not nk:
        return {"status": "unknown", "approved": False, "claim_sent": False}
    return {
        "status": nk.get("didit_status") or "unknown",
        "approved": nk.get("didit_status") == "Approved",
        "claim_sent": bool(nk.get("claim_token_hash") or nk.get("claim_token_used_at")),
    }
