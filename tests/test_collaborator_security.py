from app.auth.collaborator_security import (
    collaborator_needs_mfa_enroll,
    collaborator_needs_password_change,
    collaborator_setup_payload,
    first_login_invite_fields,
)


def test_new_invite_requires_password_and_mfa():
    user = {"role": "nextkin", **first_login_invite_fields()}
    assert collaborator_needs_password_change(user) is True
    assert collaborator_needs_mfa_enroll(user) is True
    assert collaborator_setup_payload(user)["security_setup_required"] is True


def test_after_password_change_only_mfa_remains():
    user = {
        "role": "nextkin",
        "must_change_password": False,
        "password_changed_at": "2026-01-01",
        "must_enroll_mfa": True,
    }
    assert collaborator_needs_password_change(user) is False
    assert collaborator_needs_mfa_enroll(user) is True


def test_enrolled_mfa_clears_mfa_gate():
    user = {
        "role": "nextkin",
        "must_change_password": False,
        "password_changed_at": "2026-01-01",
        "must_enroll_mfa": True,
        "mfa_methods": {"email": True, "authenticator": False, "sms": False},
        "didit_status": "Approved",
    }
    assert collaborator_needs_mfa_enroll(user) is False
    assert collaborator_setup_payload(user)["must_verify_identity"] is False
    assert collaborator_setup_payload(user)["security_setup_required"] is False


def test_family_never_needs_identity_gate(monkeypatch):
    from app.auth.collaborator_security import collaborator_needs_identity_verification

    monkeypatch.setattr("app.auth.didit.claims_require_didit", lambda: True)
    family = {
        "role": "nextkin",
        "access_type": "family",
        "must_change_password": False,
        "password_changed_at": "2026-01-01",
        "mfa_methods": {"email": True},
    }
    assert collaborator_needs_identity_verification(family) is False


def test_nok_needs_identity_until_approved(monkeypatch):
    from app.auth.collaborator_security import (
        collaborator_needs_identity_verification,
        collaborator_setup_payload,
    )

    monkeypatch.setattr("app.auth.didit.claims_require_didit", lambda: True)
    user = {
        "role": "nextkin",
        "access_type": "nextkin",
        "must_change_password": False,
        "password_changed_at": "2026-01-01",
        "mfa_methods": {"email": True},
    }
    assert collaborator_needs_identity_verification(user) is True
    assert collaborator_setup_payload(user)["must_verify_identity"] is True
    user["didit_status"] = "Approved"
    assert collaborator_needs_identity_verification(user) is False


def test_returning_legacy_user_is_not_forced():
    user = {"role": "nextkin", "last_login_at": "2026-01-01"}
    assert collaborator_needs_password_change(user) is False
    assert collaborator_needs_mfa_enroll(user) is False


def test_never_logged_in_legacy_invite_is_forced():
    user = {"role": "nextkin"}
    assert collaborator_needs_password_change(user) is True
    assert collaborator_needs_mfa_enroll(user) is True


def test_owners_are_not_gated():
    user = {"role": "owner"}
    assert collaborator_needs_password_change(user) is False
    assert collaborator_setup_payload(user)["security_setup_required"] is False


def test_password_reset_identity_includes_family_role():
    from app.auth.collaborator_security import (
        collaborator_access_level_label,
        password_reset_identity,
        should_send_owner_access_alert,
    )

    identity = password_reset_identity(
        {
            "role": "nextkin",
            "access_type": "family",
            "full_name": "Sebastian",
            "email": "sebastian@example.com",
            "portal_role": "super_admin",
        }
    )
    assert identity["full_name"] == "Sebastian"
    assert identity["portal_role_label"] == "Super Admin"
    assert identity["access_type"] == "family"
    assert password_reset_identity({"role": "owner", "email": "a@b.c"}) == {
        "full_name": None,
        "email": "a@b.c",
    }
    family = {
        "role": "nextkin",
        "access_type": "family",
        "portal_role": "admin",
    }
    assert collaborator_access_level_label(family) == "Admin"
    assert should_send_owner_access_alert(family) is False
    assert should_send_owner_access_alert(
        {"role": "nextkin", "access_type": "nextkin"}
    ) is True
    assert should_send_owner_access_alert(
        {
            "role": "nextkin",
            "access_type": "nextkin",
            "owner_access_alert_sent_at": "2026-01-01",
        }
    ) is False


def test_owner_first_access_claim_uses_nin_not_ne():
    from bson import ObjectId
    from app.auth.collaborator_security import owner_nok_first_access_claim_filter

    owner_id = ObjectId()
    nextkin_id = "abc123"
    query = owner_nok_first_access_claim_filter(
        owner_id=owner_id,
        nextkin_id=nextkin_id,
    )
    assert query["_id"] == owner_id
    nin_clause = next(
        clause
        for clause in query["$or"]
        if isinstance(clause.get("nok_first_access_alert_ids"), dict)
        and "$nin" in clause["nok_first_access_alert_ids"]
    )
    assert nextkin_id in nin_clause["nok_first_access_alert_ids"]["$nin"]
    serialized = str(query)
    assert "$ne" not in serialized


def test_collaborator_change_password_reads_nok_session_cookie():
    import inspect

    from app.auth.routes import (
        collaborator_change_password,
        get_authorized_user_for_email,
    )

    src = inspect.getsource(collaborator_change_password)
    assert "decode_owner_or_nok_token" in src
    assert "decode_access_token(request, authorization)" not in src
    assert "decode_owner_or_nok_token" in inspect.getsource(
        get_authorized_user_for_email
    )
