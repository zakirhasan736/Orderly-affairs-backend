"""Full owner account wipe: Cloudinary, vault AI uploads, Mongo, Stripe.

Retains a hashed identity tombstone in `deleted_accounts` for rejoin detection.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import stripe
from bson import ObjectId

from app.auth.deleted_account_registry import record_deleted_account
from app.config import settings
from app.database import (
    access_logs_collection,
    ai_documents_collection,
    ai_skill_examples_collection,
    auth_rate_limits_collection,
    feedback_collection,
    kits_collection,
    letters_collection,
    messageofnextkin_collection,
    nok_letters_collection,
    onboarding_progress,
    otp_collection,
    otp_fraud_logs_collection,
    otp_send_locks_collection,
    otp_verify_locks_collection,
    pending_signup_collection,
    refresh_tokens_collection,
    scheduled_letters_collection,
    section_data_collection,
    section_footprints_collection,
    sms_mfa_attempts_collection,
    support_messages_collection,
    support_threads_collection,
    users_collection,
    vault_audit_logs_collection,
)
from app.security.cloudinary_purge import purge_owner_cloudinary_media
from app.security.refresh_tokens import revoke_all_user_refresh_tokens
from app.storage.vault import purge_owner_vault_dir

stripe.api_key = settings.STRIPE_SECRET_KEY

DELETE_CONFIRM_PHRASE = "DELETE"


def _safe_delete_path(path_value: str | None) -> None:
    if not path_value:
        return
    try:
        path = Path(path_value)
    except Exception:
        return
    try:
        if path.exists() and path.is_file():
            path.unlink()
    except Exception:
        pass


def _collect_media_public_ids(docs: list[dict]) -> list[str]:
    ids: list[str] = []
    for doc in docs:
        media = doc.get("media")
        if isinstance(media, dict) and media.get("public_id"):
            ids.append(str(media["public_id"]))
        for key in ("attachments", "files", "uploads"):
            items = doc.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("public_id"):
                    ids.append(str(item["public_id"]))
    return ids


async def _cancel_stripe_for_owner(owner: dict) -> dict:
    billing = owner.get("billing") or {}
    summary: dict[str, Any] = {
        "subscription_canceled": False,
        "customer_deleted": False,
        "errors": [],
    }

    subscription_id = billing.get("subscription_id")
    customer_id = billing.get("customer_id")

    if subscription_id:
        try:
            stripe.Subscription.cancel(subscription_id)
            summary["subscription_canceled"] = True
        except Exception as exc:
            summary["errors"].append(f"subscription: {exc}")

    if customer_id:
        try:
            stripe.Customer.delete(customer_id)
            summary["customer_deleted"] = True
        except Exception as exc:
            summary["errors"].append(f"customer: {exc}")

    return summary


async def _purge_ai_documents(owner: dict) -> tuple[int, dict[str, int]]:
    from app.ai.ai_document_storage import destroy_ai_document_assets
    from app.storage.message_s3 import purge_owner_message_s3_prefix
    from app.storage.section_s3 import purge_owner_section_s3_prefix
    from app.storage.vault_s3 import purge_owner_vault_s3_prefix

    owner_id = str(owner["_id"])
    deleted = 0
    cursor = ai_documents_collection.find({"user_id": owner_id})
    async for doc in cursor:
        destroy_ai_document_assets(doc)
        await ai_documents_collection.delete_one({"_id": doc["_id"]})
        deleted += 1
    await purge_owner_vault_dir(owner)

    s3_counts: dict[str, int] = {}
    errors: list[str] = []

    try:
        s3_counts["vault"] = purge_owner_vault_s3_prefix(
            folder_uuid=owner.get("folder_uuid")
        )
    except Exception as exc:
        errors.append(f"vault_s3: {exc}")

    try:
        s3_counts["messages"] = purge_owner_message_s3_prefix(
            folder_uuid=owner.get("folder_uuid")
        )
    except Exception as exc:
        errors.append(f"message_s3: {exc}")

    try:
        s3_counts["sections"] = purge_owner_section_s3_prefix(
            owner_email=owner.get("email")
        )
    except Exception as exc:
        errors.append(f"section_s3: {exc}")

    if errors:
        raise RuntimeError(
            "Account purge aborted: S3 prefix wipe failed — "
            + "; ".join(errors)
        )

    return deleted, s3_counts


async def _delete_count(collection, query: dict) -> int:
    return (await collection.delete_many(query)).deleted_count


async def purge_owner_account(
    owner: dict,
    *,
    deleted_by: str = "self",
    reason: str | None = None,
    deleted_by_email: str | None = None,
) -> dict:
    """
    Irreversibly remove an owner and all owned data:
    - Cloudinary orderly_affairs/{email}/ folder
    - Message / letter media public_ids
    - VPS vault AI autofill uploads + S3 prefixes (fail-closed)
    - Mongo: sections, kits, letters, NOK letters, messages, family/NOKs,
      tokens, support, OTPs, feedback, footprints, audit trails
    - Stripe subscription + customer (best effort)
    - Retains hashed email/phone tombstone for rejoin detection
    """
    owner_id = str(owner["_id"])
    email = str(owner.get("email") or "").strip().lower()
    phone = str(owner.get("phone") or owner.get("phone_number") or "").strip()
    owner_refs = list({owner_id, email}) if email else [owner_id]

    # Collect media public_ids before deleting Mongo docs.
    messages = await messageofnextkin_collection.find(
        {"owner_id": {"$in": owner_refs}},
    ).to_list(length=10_000)
    letter_docs = await letters_collection.find(
        {"owner_id": {"$in": owner_refs}},
    ).to_list(length=10_000)
    nok_letter_docs = await nok_letters_collection.find(
        {"owner_id": {"$in": owner_refs}},
    ).to_list(length=10_000)
    media_ids = _collect_media_public_ids(
        messages + letter_docs + nok_letter_docs
    )

    # Fail-closed media wipe first so a failed purge can be retried cleanly.
    cloudinary_summary = purge_owner_cloudinary_media(
        owner_email=email,
        message_public_ids=media_ids,
    )
    ai_deleted, s3_counts = await _purge_ai_documents(owner)
    stripe_summary = await _cancel_stripe_for_owner(owner)

    # Hashed identity fingerprint for future rejoin detection (no vault content).
    tombstone = await record_deleted_account(
        owner,
        deleted_by=deleted_by,
        reason=reason,
        deleted_by_email=deleted_by_email,
    )

    # Linked NOK / family collaborator accounts.
    nextkin_users = await users_collection.find(
        {"role": "nextkin", "owner_id": owner_id},
        {"_id": 1},
    ).to_list(length=5_000)
    nextkin_ids = [str(doc["_id"]) for doc in nextkin_users]
    for nk_id in nextkin_ids:
        await revoke_all_user_refresh_tokens(nk_id, role="nextkin")

    await revoke_all_user_refresh_tokens(owner_id)

    nextkin_result = await users_collection.delete_many(
        {"role": "nextkin", "owner_id": owner_id},
    )

    mongo_counts = {
        "sections": await _delete_count(
            section_data_collection, {"owner_id": {"$in": owner_refs}}
        ),
        "kits": await _delete_count(
            kits_collection, {"owner_id": {"$in": owner_refs}}
        ),
        "letters": await _delete_count(
            letters_collection, {"owner_id": {"$in": owner_refs}}
        ),
        "nok_letters": await _delete_count(
            nok_letters_collection, {"owner_id": {"$in": owner_refs}}
        ),
        "scheduled_letters": await _delete_count(
            scheduled_letters_collection, {"owner_id": {"$in": owner_refs}}
        ),
        "messages": await _delete_count(
            messageofnextkin_collection, {"owner_id": {"$in": owner_refs}}
        ),
        "onboarding": await _delete_count(
            onboarding_progress,
            {
                "$or": [
                    {"owner_id": {"$in": owner_refs}},
                    {"user_id": {"$in": owner_refs}},
                ]
            },
        ),
        "refresh_tokens": await _delete_count(
            refresh_tokens_collection,
            {
                "$or": [
                    {"user_id": {"$in": owner_refs + nextkin_ids}},
                    {"email": email} if email else {"user_id": owner_id},
                ]
            },
        ),
        "nextkin_users": nextkin_result.deleted_count,
        "ai_documents": ai_deleted,
        "ai_skill_examples": await _delete_count(
            ai_skill_examples_collection, {"user_id": owner_id}
        ),
        "section_footprints": await _delete_count(
            section_footprints_collection, {"owner_id": {"$in": owner_refs}}
        ),
        "access_logs": await _delete_count(
            access_logs_collection,
            {
                "$or": [
                    {"user_id": {"$in": owner_refs + nextkin_ids}},
                    {"owner_id": {"$in": owner_refs}},
                ]
            },
        ),
        "vault_audit_logs": await _delete_count(
            vault_audit_logs_collection,
            {
                "$or": [
                    {"owner_id": {"$in": owner_refs}},
                    {"actor_id": {"$in": owner_refs + nextkin_ids}},
                ]
            },
        ),
        "pending_signups": (
            await _delete_count(pending_signup_collection, {"email": email})
            if email
            else 0
        ),
        "otps": (
            await _delete_count(otp_collection, {"email": email}) if email else 0
        ),
        "sms_mfa_attempts": 0,
        "otp_fraud_logs": 0,
        "otp_locks": 0,
        "auth_rate_limits": 0,
        "feedback": await _delete_count(
            feedback_collection,
            {
                "$or": [
                    {"owner_id": {"$in": owner_refs}},
                    {"user_id": {"$in": owner_refs}},
                    *([{"email": email}] if email else []),
                ]
            },
        ),
    }

    phone_or_email_filters: list[dict[str, Any]] = []
    if email:
        phone_or_email_filters.append({"email": email})
    if phone:
        phone_or_email_filters.append({"phone": phone})

    if phone_or_email_filters:
        mongo_counts["sms_mfa_attempts"] = await _delete_count(
            sms_mfa_attempts_collection, {"$or": phone_or_email_filters}
        )
        mongo_counts["otp_fraud_logs"] = await _delete_count(
            otp_fraud_logs_collection, {"$or": phone_or_email_filters}
        )

    # OTP / auth rate-limit keys often embed email or phone.
    lock_key_parts = [p for p in (email, phone, owner_id) if p]
    if lock_key_parts:
        key_regex = "|".join(
            part.replace("+", r"\+") for part in lock_key_parts if part
        )
        if key_regex:
            key_query = {"key": {"$regex": key_regex}}
            mongo_counts["otp_locks"] = (
                await _delete_count(otp_send_locks_collection, key_query)
                + await _delete_count(otp_verify_locks_collection, key_query)
            )
            mongo_counts["auth_rate_limits"] = await _delete_count(
                auth_rate_limits_collection, key_query
            )

    thread_filter: dict[str, Any] = {
        "$or": [
            {"owner_id": {"$in": owner_refs}},
            {"user_id": {"$in": owner_refs}},
        ],
    }
    if email:
        thread_filter["$or"].append({"email": email})
        thread_filter["$or"].append({"owner_email": email})

    thread_ids: list[Any] = []
    async for thread in support_threads_collection.find(thread_filter, {"_id": 1}):
        thread_ids.append(thread["_id"])

    if thread_ids:
        mongo_counts["support_messages"] = await _delete_count(
            support_messages_collection, {"thread_id": {"$in": thread_ids}}
        )
        mongo_counts["support_threads"] = await _delete_count(
            support_threads_collection, {"_id": {"$in": thread_ids}}
        )
    else:
        mongo_counts["support_messages"] = 0
        mongo_counts["support_threads"] = 0

    await users_collection.delete_one({"_id": ObjectId(owner_id)})

    return {
        "owner_id": owner_id,
        "email": email,
        "purged_at": datetime.utcnow().isoformat() + "Z",
        "deleted_by": deleted_by,
        "tombstone": {
            "email_hint": tombstone.get("email_hint"),
            "phone_hint": tombstone.get("phone_hint"),
            "block_rejoin": True,
        },
        "cloudinary": cloudinary_summary,
        "stripe": stripe_summary,
        "s3": s3_counts,
        "mongo": mongo_counts,
    }
