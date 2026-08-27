"""First-login password + MFA requirements for family collaborators and NOK."""

from __future__ import annotations

import hmac
from datetime import datetime


def _has_enrolled_mfa(user: dict) -> bool:
    methods = user.get("mfa_methods") or {}
    if isinstance(methods, dict) and any(bool(v) for v in methods.values()):
        return True
    return False


def collaborator_needs_password_change(user: dict | None) -> bool:
    """True until this person has chosen their own password."""
    if not user or user.get("role") != "nextkin":
        return False
    if user.get("password_changed_at"):
        return False
    if user.get("must_change_password") is False:
        return False
    if user.get("must_change_password") is True:
        return True
    # Invited but never signed in (legacy records without the flag).
    return not user.get("last_login_at")


def collaborator_needs_mfa_enroll(user: dict | None) -> bool:
    """True until they enroll at least one MFA method in settings (not login OTP)."""
    if not user or user.get("role") != "nextkin":
        return False
    if _has_enrolled_mfa(user):
        return False
    if user.get("must_enroll_mfa") is False:
        return False
    if user.get("must_enroll_mfa") is True:
        return True
    return not user.get("last_login_at")


def collaborator_needs_identity_verification(user: dict | None) -> bool:
    """True until this next of kin has a Didit Approved ID + selfie.

    Family collaborators never take this step. When Didit is not configured
    (typical local dev), the gate is skipped so they are not trapped.
    """
    if not user or user.get("role") != "nextkin":
        return False
    from app.auth.access_types import is_family_collaborator
    from app.auth.after_death_policy import didit_is_approved
    from app.auth.didit import claims_require_didit

    if is_family_collaborator(user):
        return False
    if not claims_require_didit():
        return False
    return not didit_is_approved(user.get("didit_status"))


def collaborator_setup_payload(user: dict | None) -> dict:
    must_pw = collaborator_needs_password_change(user)
    must_mfa = collaborator_needs_mfa_enroll(user)
    must_kyc = collaborator_needs_identity_verification(user)
    return {
        "must_change_password": must_pw,
        "must_enroll_mfa": must_mfa,
        "must_verify_identity": must_kyc,
        "didit_status": (user or {}).get("didit_status") or None,
        "security_setup_required": must_pw or must_mfa or must_kyc,
    }


def first_login_invite_fields() -> dict:
    return {
        "must_change_password": True,
        "must_enroll_mfa": True,
    }


def _plain_secret_matches(stored: str, offered: str) -> bool:
    left = stored.encode("utf-8")
    right = offered.encode("utf-8")
    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def collaborator_login_password_ok(user: dict | None, plain: str) -> bool:
    """Accept stored hashes or the invite plaintext `master_password`.

    Living NOK credentials live in encrypted_profile as plaintext plus
    password_hash. Login used to check only the hash on the raw Mongo
    document, so a missing/stale hash with a valid invite password 401'd.
    """
    if not user or not str(plain or "").strip():
        return False

    from app.security.nextkin_profile_crypto import load_nextkin_profile
    from app.security.password_handler import verify_password

    profile = load_nextkin_profile(dict(user)) or dict(user)
    offered = str(plain).strip()

    for key in ("password_hash", "password"):
        stored = profile.get(key) or ""
        if isinstance(stored, str) and stored and verify_password(offered, stored):
            return True

    master = str(profile.get("master_password") or "").strip()
    if master and _plain_secret_matches(master, offered):
        return True
    return False


def collaborator_has_login_secret(user: dict | None) -> bool:
    if not user:
        return False
    from app.security.nextkin_profile_crypto import load_nextkin_profile

    profile = load_nextkin_profile(dict(user)) or dict(user)
    return bool(
        str(profile.get("password_hash") or "").strip()
        or str(profile.get("password") or "").strip()
        or str(profile.get("master_password") or "").strip()
    )


def password_reset_identity(user: dict | None) -> dict:
    """Safe display fields for the reset-password UI (name + access level)."""
    if not user:
        return {}
    from app.auth.portal_roles import role_label

    access_type = str(user.get("access_type") or "").lower()
    payload: dict = {
        "full_name": user.get("full_name") or None,
        "email": user.get("email"),
    }
    if user.get("role") == "nextkin" and access_type == "family":
        payload["access_type"] = "family"
        payload["portal_role"] = user.get("portal_role")
        payload["portal_role_label"] = role_label(user.get("portal_role"))
    elif user.get("role") == "nextkin":
        payload["access_type"] = "nextkin"
        payload["portal_role_label"] = "Next of Kin"
    return payload


def password_changed_fields() -> dict:
    return {
        "must_change_password": False,
        "password_changed_at": datetime.utcnow(),
    }


def collaborator_access_level_label(user: dict | None) -> str | None:
    label = password_reset_identity(user).get("portal_role_label")
    return str(label) if label else None


def should_send_owner_access_alert(user: dict | None) -> bool:
    """Owner kit-open email: first completed NOK sign-in only — never family."""
    if not user or user.get("role") != "nextkin":
        return False
    from app.auth.access_types import (
        ACCESS_TYPE_FAMILY,
        is_family_collaborator,
        is_nextkin_collaborator,
    )

    access_type = str(user.get("access_type") or "").strip().lower()
    if access_type == ACCESS_TYPE_FAMILY or is_family_collaborator(user):
        return False
    if not is_nextkin_collaborator(user):
        return False
    if user.get("owner_access_alert_sent_at"):
        return False
    return True


def owner_nok_first_access_claim_filter(*, owner_id, nextkin_id: str) -> dict:
    """Match an owner who has not yet been alerted for this next of kin.

    Must use $nin on the array. {$ne: id} compares the whole array to the id
    string, so it still matches after the id is stored and re-sends the email.
    """
    nid = str(nextkin_id)
    not_yet = [nid]
    try:
        from bson import ObjectId
        from bson.errors import InvalidId

        oid = ObjectId(nid)
        if oid not in not_yet:
            not_yet.append(oid)
    except (InvalidId, TypeError, ValueError):
        pass
    return {
        "_id": owner_id,
        "role": "owner",
        "$or": [
            {"nok_first_access_alert_ids": {"$exists": False}},
            {"nok_first_access_alert_ids": None},
            {"nok_first_access_alert_ids": []},
            {"nok_first_access_alert_ids": {"$nin": not_yet}},
        ],
    }


def mfa_enrolled_fields() -> dict:
    return {
        "must_enroll_mfa": False,
    }
