"""Didit KYC (ID + liveness) for after-death next-of-kin / executor claims.

Living NOK release does not use this. Claim links are minted only after
status Approved, and only once an admin has released the vault (owner_status
deceased). Inconclusive/declined sessions stay locked for human review.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bson import ObjectId
from bson.errors import InvalidId

from app.config import nextkin_didit_callback_url, settings
from app.database import users_collection

DIDIT_APPROVED = "Approved"
UNFINISHED = frozenset(
    {"Not Started", "In Progress", "Resubmitted", "Awaiting User"}
)


def didit_configured() -> bool:
    return bool(
        (settings.DIDIT_API_KEY or "").strip()
        and (settings.DIDIT_WORKFLOW_ID or "").strip()
    )


def claims_require_didit() -> bool:
    """Production always requires KYC. Locally only when API keys are set."""
    env = (settings.APP_ENV or "").strip().lower()
    if env in {"production", "prod", "staging"}:
        return True
    return didit_configured()


def webhook_secret() -> str:
    return (settings.DIDIT_WEBHOOK_SECRET or "").strip()


def shorten_floats(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: shorten_floats(v) for k, v in data.items()}
    if isinstance(data, list):
        return [shorten_floats(x) for x in data]
    if isinstance(data, float) and data.is_integer():
        return int(data)
    return data


def verify_webhook_signature(
    *,
    body: dict,
    signature_v2: str | None,
    signature_simple: str | None,
    signature_raw: str | None,
    timestamp_header: str | None,
    raw_body: bytes,
    secret: str,
) -> bool:
    if not secret or not timestamp_header:
        return False
    try:
        if abs(int(time.time()) - int(timestamp_header)) > 300:
            return False
    except (TypeError, ValueError):
        return False

    if signature_v2 and _hmac_hex_ok(
        secret,
        json.dumps(
            shorten_floats(body),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8"),
        signature_v2,
    ):
        return True
    if signature_simple and _hmac_hex_ok(
        secret,
        ":".join(
            [
                str(body.get("timestamp", "")),
                str(body.get("session_id", "")),
                str(body.get("status", "")),
                str(body.get("webhook_type", "")),
            ]
        ).encode("utf-8"),
        signature_simple,
    ):
        return True
    if signature_raw and _hmac_hex_ok(secret, raw_body, signature_raw):
        return True
    return False


def _hmac_hex_ok(secret: str, message: bytes, header: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest((header or "").strip(), expected)


def _split_name(full_name: str) -> tuple[str, str]:
    parts = str(full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def post_didit_json(path: str, payload: dict) -> dict:
    api_key = (settings.DIDIT_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError("Didit is not configured")
    base = (settings.DIDIT_API_BASE or "https://verification.didit.me").rstrip("/")
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        f"{base}{path}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Didit API error ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Didit is unreachable: {exc.reason}") from exc


def _post_json(path: str, payload: dict) -> dict:
    return post_didit_json(path, payload)


def session_public_payload(nextkin: dict) -> dict:
    status = nextkin.get("didit_status") or "Not Started"
    url = nextkin.get("didit_session_url") or ""
    approved = status == DIDIT_APPROVED
    return {
        "configured": didit_configured(),
        "status": status,
        "approved": approved,
        "session_url": None if approved else url or None,
        "verified_at": nextkin.get("didit_verified_at"),
    }


async def create_or_reuse_session(*, nextkin: dict, owner: dict) -> dict:
    if not didit_configured():
        raise RuntimeError(
            "Identity verification is not configured. Add DIDIT_API_KEY and "
            "DIDIT_WORKFLOW_ID, then set DIDIT_WEBHOOK_SECRET from the Didit "
            "webhook destination."
        )
    if nextkin.get("didit_status") == DIDIT_APPROVED and nextkin.get(
        "didit_session_url"
    ):
        return session_public_payload(nextkin)

    from app.auth.claimant_roles import didit_purpose

    first, last = _split_name(str(nextkin.get("full_name") or ""))
    vendor_data = str(nextkin["_id"])
    payload: dict[str, Any] = {
        "workflow_id": settings.DIDIT_WORKFLOW_ID.strip(),
        "callback": nextkin_didit_callback_url(),
        "vendor_data": vendor_data,
        "metadata": {
            "purpose": didit_purpose(nextkin),
            "owner_id": str(owner.get("_id") or nextkin.get("owner_id") or ""),
            "nextkin_id": vendor_data,
        },
        "contact_details": {
            "email": nextkin.get("email"),
            "send_notification_emails": False,
        },
    }
    phone = (nextkin.get("phone_number") or "").strip()
    if phone:
        payload["contact_details"]["phone"] = phone
    expected: dict[str, Any] = {}
    if first:
        expected["first_name"] = first
    if last:
        expected["last_name"] = last
    if expected:
        payload["expected_details"] = expected

    created = _post_json("/v3/session/", payload)
    now = datetime.now(timezone.utc)
    await users_collection.update_one(
        {"_id": nextkin["_id"]},
        {
            "$set": {
                "didit_session_id": created.get("session_id"),
                "didit_session_url": created.get("url"),
                "didit_status": created.get("status") or "Not Started",
                "didit_workflow_id": created.get("workflow_id")
                or settings.DIDIT_WORKFLOW_ID,
                "updated_at": now,
            }
        },
    )
    nextkin["didit_session_id"] = created.get("session_id")
    nextkin["didit_session_url"] = created.get("url")
    nextkin["didit_status"] = created.get("status") or "Not Started"
    return session_public_payload(nextkin)


async def start_death_identity_for_owner(*, owner: dict) -> int:
    """Open Didit sessions for every named NOK after a passing is reported."""
    if not didit_configured():
        return 0

    from app.auth.access_types import ACCESS_TYPE_FAMILY, resolve_access_type
    from app.notifications.nextkin_emails import NextKinEmailEvent, send_nextkin_email

    started = 0
    cursor = users_collection.find(
        {
            "role": "nextkin",
            "owner_id": str(owner["_id"]),
            "access_revoked": {"$ne": True},
        }
    )
    async for nk in cursor:
        if resolve_access_type(nk) == ACCESS_TYPE_FAMILY:
            continue
        if nk.get("didit_status") == DIDIT_APPROVED:
            continue
        try:
            session = await create_or_reuse_session(nextkin=nk, owner=owner)
        except Exception as exc:
            print(f"⚠️ Didit session failed for {nk.get('_id')}: {exc}")
            continue
        started += 1
        url = session.get("session_url")
        if not url:
            continue
        try:
            await send_nextkin_email(
                event=NextKinEmailEvent.IDENTITY_VERIFY,
                nextkin=nk,
                owner=owner,
                verify_url=url,
            )
        except Exception as exc:
            print(f"⚠️ Didit verify email failed for {nk.get('_id')}: {exc}")
    return started


async def apply_didit_status(
    *,
    session_id: str | None,
    vendor_data: str | None,
    status: str,
    event_id: str | None,
    decision: dict | None,
) -> dict | None:
    query: dict[str, Any] = {"role": "nextkin"}
    if vendor_data:
        try:
            query["_id"] = ObjectId(str(vendor_data))
        except (InvalidId, TypeError):
            query["didit_session_id"] = session_id
    elif session_id:
        query["didit_session_id"] = session_id
    else:
        return None

    nk = await users_collection.find_one(query)
    if not nk and session_id:
        nk = await users_collection.find_one(
            {"role": "nextkin", "didit_session_id": session_id}
        )
    if not nk:
        return None
    if event_id and nk.get("didit_last_event_id") == event_id:
        return nk

    now = datetime.now(timezone.utc)
    fields: dict[str, Any] = {
        "didit_status": status,
        "didit_last_event_id": event_id,
        "updated_at": now,
    }
    if session_id:
        fields["didit_session_id"] = session_id
    if status == DIDIT_APPROVED:
        fields["didit_verified_at"] = now
    if decision:
        fields["didit_decision_status"] = status

    await users_collection.update_one({"_id": nk["_id"]}, {"$set": fields})
    nk.update(fields)

    from app.auth.after_death_case import apply_claimant_didit, open_case_for_owner

    owner_id = str(nk.get("owner_id") or "")
    case = await open_case_for_owner(owner_id) if owner_id else None
    await apply_claimant_didit(case, nk, status)

    if status == DIDIT_APPROVED:
        from app.auth.service import grant_upon_death_access

        if owner_id:
            await grant_upon_death_access(owner_id, nextkin_id=nk["_id"])
    return nk


async def find_by_session_id(session_id: str) -> dict | None:
    if not session_id:
        return None
    return await users_collection.find_one(
        {"role": "nextkin", "didit_session_id": session_id}
    )
