"""Full owner account wipe: Cloudinary, vault AI uploads, Mongo, Stripe."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import stripe
from bson import ObjectId

from app.config import settings
from app.database import (
    ai_documents_collection,
    kits_collection,
    letters_collection,
    messageofnextkin_collection,
    onboarding_progress,
    otp_collection,
    pending_signup_collection,
    refresh_tokens_collection,
    section_data_collection,
    support_messages_collection,
    support_threads_collection,
    users_collection,
)
from app.security.cloudinary_purge import purge_owner_cloudinary_media
from app.storage.vault import purge_owner_vault_dir

stripe.api_key = settings.STRIPE_SECRET_KEY

DELETE_CONFIRM_PHRASE = "DELETE"


def _safe_delete_path(path_value: str | None) -> None:
    if not path_value:
        return
    try:
        path = Path(path_value)
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
        # Nested attachments / gallery arrays if present.
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
            # Already canceled / missing is fine.
            summary["errors"].append(f"subscription: {exc}")

    if customer_id:
        try:
            stripe.Customer.delete(customer_id)
            summary["customer_deleted"] = True
        except Exception as exc:
            summary["errors"].append(f"customer: {exc}")

    return summary


async def _purge_ai_documents(owner: dict) -> int:
    from app.ai.ai_document_storage import destroy_ai_document_assets

    owner_id = str(owner["_id"])
    deleted = 0
    cursor = ai_documents_collection.find({"user_id": owner_id})
    async for doc in cursor:
        destroy_ai_document_assets(doc)
        await ai_documents_collection.delete_one({"_id": doc["_id"]})
        deleted += 1
    # Remove the whole vault folder (covers legacy orphans + uuid layout).
    await purge_owner_vault_dir(owner)
    return deleted


async def purge_owner_account(owner: dict) -> dict:
    """
    Irreversibly remove an owner and all owned data:
    - Cloudinary orderly_affairs/{email}/ folder
    - Message / letter media public_ids
    - VPS vault AI autofill uploads
    - Mongo: sections, kits, letters, messages, NOKs, tokens, support, OTPs
    - Stripe subscription + customer (best effort)
    """
    owner_id = str(owner["_id"])
    email = str(owner.get("email") or "").strip().lower()
    owner_refs = list({owner_id, email}) if email else [owner_id]

    # Collect media public_ids before deleting Mongo docs.
    messages = await messageofnextkin_collection.find(
        {"owner_id": {"$in": owner_refs}},
    ).to_list(length=10_000)
    letter_docs = await letters_collection.find(
        {"owner_id": {"$in": owner_refs}},
    ).to_list(length=10_000)
    media_ids = _collect_media_public_ids(messages + letter_docs)

    cloudinary_summary = purge_owner_cloudinary_media(
        owner_email=email,
        message_public_ids=media_ids,
    )
    ai_deleted = await _purge_ai_documents(owner)
    stripe_summary = await _cancel_stripe_for_owner(owner)

    # Next-of-kin accounts linked to this owner.
    nextkin_result = await users_collection.delete_many(
        {"role": "nextkin", "owner_id": owner_id},
    )

    mongo_counts = {
        "sections": (
            await section_data_collection.delete_many(
                {"owner_id": {"$in": owner_refs}},
            )
        ).deleted_count,
        "kits": (
            await kits_collection.delete_many({"owner_id": {"$in": owner_refs}})
        ).deleted_count,
        "letters": (
            await letters_collection.delete_many({"owner_id": {"$in": owner_refs}})
        ).deleted_count,
        "messages": (
            await messageofnextkin_collection.delete_many(
                {"owner_id": {"$in": owner_refs}},
            )
        ).deleted_count,
        "onboarding": (
            await onboarding_progress.delete_many({"owner_id": {"$in": owner_refs}})
        ).deleted_count,
        "refresh_tokens": (
            await refresh_tokens_collection.delete_many(
                {"user_id": {"$in": owner_refs}},
            )
        ).deleted_count,
        "nextkin_users": nextkin_result.deleted_count,
        "ai_documents": ai_deleted,
        "pending_signups": (
            await pending_signup_collection.delete_many({"email": email})
        ).deleted_count
        if email
        else 0,
        "otps": (
            await otp_collection.delete_many({"email": email})
        ).deleted_count
        if email
        else 0,
    }

    # Support threads owned by this user (best effort by email / user_id).
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
        mongo_counts["support_messages"] = (
            await support_messages_collection.delete_many(
                {"thread_id": {"$in": thread_ids}},
            )
        ).deleted_count
        mongo_counts["support_threads"] = (
            await support_threads_collection.delete_many(
                {"_id": {"$in": thread_ids}},
            )
        ).deleted_count
    else:
        mongo_counts["support_messages"] = 0
        mongo_counts["support_threads"] = 0

    await users_collection.delete_one({"_id": ObjectId(owner_id)})

    return {
        "owner_id": owner_id,
        "email": email,
        "purged_at": datetime.utcnow().isoformat() + "Z",
        "cloudinary": cloudinary_summary,
        "stripe": stripe_summary,
        "mongo": mongo_counts,
    }
