from datetime import datetime, timezone

from bson import ObjectId

from app.database import db, messageofnextkin_collection, users_collection
from app.letters.email_utils import render_email_html, send_email
from app.security.nok_letter_crypto import load_nok_letter
from app.nexrkinmessage.sender import send_letter
from app.notifications.nextkin_emails import NextKinEmailEvent, send_nextkin_email

nok_letters_collection = db["nok_letters"]


async def _owner_refs(owner_ref: str) -> list[str]:
    refs = {owner_ref}
    owner = await users_collection.find_one({"email": owner_ref, "role": "owner"})

    if not owner:
        try:
            owner = await users_collection.find_one(
                {"_id": ObjectId(owner_ref), "role": "owner"}
            )
        except Exception:
            owner = None

    if owner:
        refs.add(str(owner["_id"]))
        if owner.get("email"):
            refs.add(owner["email"])

    return list(refs)


async def _resolve_owner(owner_ref: str) -> dict | None:
    owner_refs = await _owner_refs(owner_ref)

    for ref in owner_refs:
        owner = None
        try:
            owner = await users_collection.find_one(
                {"_id": ObjectId(ref), "role": "owner"}
            )
        except Exception:
            owner = None

        if not owner:
            owner = await users_collection.find_one(
                {"email": ref, "role": "owner"}
            )

        if owner:
            return owner

    return None


async def grant_upon_death_access(owner_ref: str, nextkin_id=None) -> int:
    """Email one-time claim links to upon-death NOK after Didit Approved + admin release."""
    owner = await _resolve_owner(owner_ref)
    if not owner:
        print(f"⚠️ grant_upon_death_access: owner not found for {owner_ref}")
        return 0
    if owner.get("owner_status") != "deceased":
        return 0

    owner_id = str(owner["_id"])
    from app.auth.after_death_case import cases_collection, open_case_for_owner, write_audit

    death_case = await open_case_for_owner(owner_id)
    if death_case is None:
        death_case = await cases_collection.find_one({"owner_id": owner_id})
    if death_case:
        if death_case.get("owner_disputed") or death_case.get("status") == "OWNER_DISPUTED":
            return 0
        if not death_case.get("admin_release"):
            return 0

    now = datetime.now(timezone.utc)
    granted = 0

    from app.auth.access_types import (
        ACCESS_TYPE_FAMILY,
        NEXTKIN_ACCESS_MONGO_FILTER,
        resolve_access_type,
    )
    from app.auth.didit import DIDIT_APPROVED, claims_require_didit

    query: dict = {
        "role": "nextkin",
        "owner_id": owner_id,
        "access_revoked": {"$ne": True},
        "$and": [NEXTKIN_ACCESS_MONGO_FILTER],
    }
    if nextkin_id is not None:
        query["_id"] = nextkin_id
    if claims_require_didit():
        query["didit_status"] = DIDIT_APPROVED

    cursor = users_collection.find(query)

    async for nk in cursor:
        if nk.get("access_timing") == "immediate":
            continue
        from app.auth.claim_tokens import (
            claim_is_expired,
            generate_claim_token,
            hash_claim_token,
            claim_expiry,
        )
        from app.config import nextkin_claim_url

        if nk.get("claim_token_used_at"):
            continue
        if nk.get("claim_token_hash") and not claim_is_expired(
            nk.get("claim_token_expires_at")
        ):
            continue

        claim_token = generate_claim_token()
        await users_collection.update_one(
            {"_id": nk["_id"]},
            {
                "$set": {
                    "claim_token_hash": hash_claim_token(claim_token),
                    "claim_token_expires_at": claim_expiry(now),
                    "must_change_password": True,
                    "must_enroll_mfa": True,
                    "updated_at": now,
                },
                "$unset": {
                    "password_hash": "",
                    "master_password": "",
                    "claim_token_used_at": "",
                },
            },
        )

        refreshed = await users_collection.find_one({"_id": nk["_id"]})
        if not refreshed:
            continue

        try:
            await send_nextkin_email(
                event=NextKinEmailEvent.OWNER_DECEASED,
                nextkin=refreshed,
                owner=owner,
                claim_url=nextkin_claim_url(claim_token),
            )
            granted += 1
            if death_case:
                await write_audit(
                    event="CLAIM_SENT",
                    case=death_case,
                    actor_type="system",
                    metadata={"nextkin_id": str(nk["_id"])},
                )
                await cases_collection.update_one(
                    {"_id": death_case["_id"]},
                    {
                        "$set": {
                            "claim_issued_at": now,
                            "claim_expires_at": claim_expiry(now),
                            "status": "CLAIM_SENT",
                        }
                    },
                )
        except Exception as e:
            print(f"⚠️ Upon-death access email failed for next-of-kin {nk.get('_id')}: {e}")

    return granted


async def mark_owner_deceased(
    *,
    owner_id: str,
    reported_by_nextkin_id: str | None,
    source: str,
) -> dict:
    owner = await _resolve_owner(owner_id)
    if not owner:
        return {"triggered": False, "reason": "owner_not_found"}

    if owner.get("owner_status") == "deceased":
        return {
            "triggered": False,
            "already_deceased": True,
            "status": "deceased",
        }

    now = datetime.now(timezone.utc)
    await users_collection.update_one(
        {"_id": owner["_id"]},
        {
            "$set": {
                "owner_status": "deceased",
                "deceased_reported_at": now,
                "deceased_reported_by": reported_by_nextkin_id,
                "deceased_detection_source": source,
                "death_report_pending": False,
                "updated_at": now,
            }
        },
    )

    death_result = await trigger_death_letters(str(owner["_id"]))
    return {
        "triggered": True,
        "status": "deceased",
        "already_deceased": False,
        "upon_death_granted": death_result.get("upon_death_granted", 0),
        "source": source,
    }


async def record_pending_death_report(
    *,
    owner: dict,
    reported_by_nextkin: dict,
    source: str = "nok_manual_report",
) -> dict:
    """NOK/attorney report. Does not release vault or letters — admin does that."""
    if owner.get("owner_status") == "deceased":
        return {
            "status": "deceased",
            "already_reported": True,
            "pending_review": False,
        }

    from app.auth.after_death_case import create_or_get_case

    already = bool(owner.get("death_report_pending"))
    now = datetime.now(timezone.utc)
    reporter_id = str(reported_by_nextkin.get("_id") or "")
    if not already:
        await users_collection.update_one(
            {"_id": owner["_id"]},
            {
                "$set": {
                    "death_report_pending": True,
                    "death_reported_at": now,
                    "death_reported_by": reporter_id,
                    "death_report_source": source,
                    "updated_at": now,
                }
            },
        )
        owner["death_report_pending"] = True

    case = await create_or_get_case(
        owner=owner,
        reporter=reported_by_nextkin,
        source=source,
    )

    try:
        from app.auth.didit import start_death_identity_for_owner

        await start_death_identity_for_owner(owner=owner)
    except Exception as exc:
        print("⚠️ Didit death-claim sessions failed:", exc)

    return {
        "status": "pending_review",
        "already_reported": already,
        "pending_review": True,
        "upon_death_granted": 0,
        "case_id": str(case.get("_id")),
        "case_reference": case.get("reference"),
    }


async def cancel_pending_death_report_on_owner_login(owner: dict) -> bool:
    """Login is not an automatic cancel. Use authenticated I Am Alive instead."""
    return False


async def trigger_death_letters(owner_id: str) -> dict:
    owner_refs = await _owner_refs(owner_id)

    letters = await messageofnextkin_collection.find({
        "owner_id": {"$in": owner_refs},
        "delivery_trigger": "death",
        "status": "pending",
        "is_deleted": False,
    }).to_list(None)

    for letter in letters:
        try:
            await send_letter(letter)
        except Exception as e:
            print(f"Failed death-trigger letter {letter['_id']}: {e}")

    nok_letters = await nok_letters_collection.find({
        "owner_id": {"$in": owner_refs},
        "delivery_status": {"$ne": "sent"},
        "$or": [
            {"delivery_trigger": "death"},
            {
                "delivery_trigger": {"$exists": False},
                "letter_date": {"$in": [None, ""]},
            },
        ],
    }).to_list(None)

    for letter in nok_letters:
        now = datetime.now(timezone.utc)
        try:
            claim = await nok_letters_collection.update_one(
                {"_id": letter["_id"], "delivery_status": {"$ne": "sent"}},
                {"$set": {"delivery_status": "processing", "updated_at": now}},
            )
            if getattr(claim, "modified_count", 0) != 1:
                continue

            letter = load_nok_letter(letter)
            to_email = letter.get("nok_email")
            if not to_email:
                raise RuntimeError("NOK email missing")

            owner_name = None
            try:
                from app.database import users_collection
                from app.notifications.display_names import resolve_owner_display_name

                owner = await users_collection.find_one(
                    {"_id": ObjectId(str(owner_id)), "role": "owner"}
                )
                if not owner:
                    owner = await users_collection.find_one(
                        {"_id": ObjectId(str(letter.get("owner_id"))), "role": "owner"}
                    )
                if owner:
                    owner_name = await resolve_owner_display_name(owner)
            except Exception:
                owner_name = None

            html = render_email_html(letter, owner_name=owner_name)
            await send_email(to_email, "A letter from your loved one", html)

            await nok_letters_collection.update_one(
                {"_id": letter["_id"]},
                {
                    "$set": {
                        "delivery_status": "sent",
                        "sent_at": now,
                        "updated_at": now,
                    }
                },
            )
        except Exception as e:
            await nok_letters_collection.update_one(
                {"_id": letter["_id"]},
                {
                    "$set": {
                        "delivery_status": "pending",
                        "last_delivery_error": str(e),
                        "updated_at": now,
                    }
                },
            )
            print(f"Failed death-trigger NOK letter {letter['_id']}: {e}")

    granted = await grant_upon_death_access(owner_id)
    if granted:
        print(f"Granted upon-death access to {granted} Next-of-Kin")

    return {"upon_death_granted": granted}


async def admin_release_nok_vault_access(
    *,
    owner_ref: str,
    admin_email: str,
    admin_id: str | None = None,
    note: str | None = None,
    ssdmf_override: bool = False,
    certificate_override: bool = False,
    wait_override: bool = False,
    death_check_override_reason: str | None = None,
) -> dict:
    """Manual after-death release. Living NOK release never calls this."""
    owner = await _resolve_owner(owner_ref)
    if not owner:
        return {"ok": False, "reason": "owner_not_found"}

    from app.auth.after_death_case import (
        apply_death_check_override,
        cas_admin_release,
        enrolled_claimants,
        maybe_mark_eligible,
        open_case_for_owner,
        write_audit,
    )

    if certificate_override or wait_override:
        return {
            "ok": False,
            "reason": "override_not_allowed",
            "message": (
                "After-death release cannot skip the death certificate or the "
                "168-hour owner protection period."
            ),
        }

    case = await open_case_for_owner(str(owner["_id"]))
    if not case:
        from app.auth.after_death_case import cases_collection

        latest = await cases_collection.find_one(
            {"owner_id": str(owner["_id"])},
            sort=[("created_at", -1)],
        )
        if latest and latest.get("owner_disputed"):
            return {
                "ok": False,
                "reason": "owner_disputed",
                "message": "The owner disputed this request. Release is frozen.",
            }
        return {
            "ok": False,
            "reason": "no_after_death_case",
            "message": "There is no open after-death access case for this owner.",
        }

    await write_audit(
        event="ADMIN_RELEASE_ATTEMPT",
        case=case,
        actor_type="admin",
        actor_id=admin_email,
    )

    if ssdmf_override:
        reason = (death_check_override_reason or note or "").strip()
        notes = (note or "").strip()
        if not reason or not notes:
            return {
                "ok": False,
                "reason": "override_incomplete",
                "message": "SSDMF override requires a reason and supporting notes.",
            }
        await apply_death_check_override(
            case=case,
            admin_email=admin_email,
            admin_id=admin_id,
            reason=reason,
            notes=notes,
        )

    claimants = await enrolled_claimants(str(owner["_id"]))
    gates = await maybe_mark_eligible(case, claimants)
    if not gates.get("eligible_for_admin_release") and not case.get("admin_release"):
        await write_audit(
            event="ADMIN_RELEASE_BLOCKED",
            case=case,
            actor_type="admin",
            actor_id=admin_email,
            metadata={"reasons": gates.get("reasons")},
        )
        return {
            "ok": False,
            "reason": "death_verification_incomplete",
            "gates": {
                "certificate_on_file": gates.get("certificate_on_file"),
                "protection_period_completed": gates.get("protection_period_completed"),
                "claimant_didit_approved": gates.get("claimant_didit_approved"),
                "owner_death_check_ok": gates.get("owner_death_check_ok"),
                "owner_disputed": gates.get("owner_disputed"),
            },
            "message": " ".join(gates.get("reasons") or []),
        }

    if case.get("owner_disputed"):
        await write_audit(
            event="ADMIN_RELEASE_BLOCKED",
            case=case,
            actor_type="admin",
            actor_id=admin_email,
            metadata={"reason": "owner_disputed"},
        )
        return {
            "ok": False,
            "reason": "owner_disputed",
            "message": "The owner disputed this request. Release is frozen.",
        }

    locked = await cas_admin_release(case=case, admin_email=admin_email)
    if not locked:
        refreshed = await open_case_for_owner(str(owner["_id"]))
        if refreshed and refreshed.get("owner_disputed"):
            return {
                "ok": False,
                "reason": "owner_disputed",
                "message": "The owner disputed this request. Release is frozen.",
            }
        if not (refreshed or case).get("admin_release"):
            return {
                "ok": False,
                "reason": "release_conflict",
                "message": "Could not complete release. Refresh and try again.",
            }

    owner_id = str(owner["_id"])
    already = owner.get("owner_status") == "deceased"
    if not already:
        death = await mark_owner_deceased(
            owner_id=owner_id,
            reported_by_nextkin_id=None,
            source=f"admin_release:{admin_email}",
        )
        granted = int(death.get("upon_death_granted") or 0)
    else:
        granted = await grant_upon_death_access(owner_id)

    return {
        "ok": True,
        "owner_id": owner_id,
        "owner_email": owner.get("email"),
        "already_deceased": already,
        "upon_death_granted": granted,
        "note": (note or "").strip() or None,
        "case_id": str(case["_id"]),
    }
