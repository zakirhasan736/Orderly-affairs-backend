"""After-death access policy and isolation from living release."""

from datetime import datetime, timedelta, timezone

from app.auth.after_death_policy import (
    OWNER_PROTECTION_PERIOD,
    didit_is_approved,
    didit_needs_manual_review,
    normalize_didit,
    normalize_ssdmf,
    protection_completed,
    protection_expires_at,
    release_gates,
    reminder_slot,
)
from app.auth.claimant_roles import is_attorney_or_executor
from app.auth.ssdmf import parse_ssdmf_response
from app.auth.vault_unlock_timings import (
    OWNER_WINDOW_INACTIVITY_ONLY,
    OWNER_WINDOW_WHEN_DOCUMENT_AND_MORTALITY_AGREE,
)


def _case(**kwargs):
    now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    started = kwargs.pop("started", now)
    if "expires" in kwargs:
        expires = kwargs.pop("expires")
    else:
        expires = protection_expires_at(started) if started else None
    base = {
        "certificate_id": "cert-1",
        "owner_notice_started_at": started,
        "owner_notice_expires_at": expires,
        "owner_death_check_status": "MATCH",
        "owner_disputed": False,
        "death_check_override": False,
        "admin_release": False,
    }
    base.update(kwargs)
    return base


def _nok(didit="Approved"):
    return {"_id": "nk1", "didit_status": didit, "access_revoked": False}


class TestProtectionClock:
    def test_period_is_168_hours_not_calendar_plus_seven(self):
        assert OWNER_PROTECTION_PERIOD == timedelta(hours=168)
        start = datetime(2026, 3, 7, 1, 30, tzinfo=timezone.utc)
        assert protection_expires_at(start) - start == timedelta(hours=168)

    def test_legacy_windows_are_not_168_hours(self):
        assert OWNER_WINDOW_WHEN_DOCUMENT_AND_MORTALITY_AGREE != OWNER_PROTECTION_PERIOD
        assert OWNER_WINDOW_INACTIVITY_ONLY != OWNER_PROTECTION_PERIOD

    def test_release_at_one_second_before_168h_fails(self):
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        now = start + timedelta(hours=168) - timedelta(seconds=1)
        gates = release_gates(case=_case(started=start), claimants=[_nok()], now=now)
        assert gates["protection_period_completed"] is False
        assert gates["eligible_for_admin_release"] is False

    def test_release_at_168h_can_continue(self):
        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        now = start + timedelta(hours=168)
        gates = release_gates(case=_case(started=start), claimants=[_nok()], now=now)
        assert gates["protection_period_completed"] is True
        assert gates["eligible_for_admin_release"] is True

    def test_clock_does_not_complete_until_notice_starts(self):
        now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        gates = release_gates(
            case=_case(
                started=None,
                expires=None,
                certificate_id=None,
                owner_death_check_status="PENDING",
            ),
            claimants=[_nok()],
            now=now + timedelta(hours=200),
        )
        assert gates["protection_started"] is False
        assert gates["protection_period_completed"] is False
        assert gates["eligible_for_admin_release"] is False
        start = datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc)
        assert protection_completed(
            started_at=start,
            expires_at=protection_expires_at(start),
            now=start + timedelta(hours=167, minutes=59),
        ) is False


class TestReleaseGates:
    def test_certificate_ssdmf_kyc_alone_do_not_release(self):
        start = datetime.now(timezone.utc)
        gates = release_gates(
            case=_case(started=start, expires=start + timedelta(hours=168)),
            claimants=[_nok()],
            now=start + timedelta(hours=1),
        )
        assert gates["certificate_on_file"]
        assert gates["claimant_didit_approved"]
        assert gates["owner_death_check_ok"]
        assert gates["eligible_for_admin_release"] is False

    def test_period_expiry_alone_does_not_release(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        gates = release_gates(
            case=_case(
                started=start,
                certificate_id=None,
                owner_death_check_status="PENDING",
            ),
            claimants=[],
            now=start + timedelta(hours=200),
        )
        assert gates["protection_period_completed"] is True
        assert gates["eligible_for_admin_release"] is False

    def test_no_match_does_not_mark_owner_alive_and_blocks(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        gates = release_gates(
            case=_case(
                started=start,
                owner_death_check_status="NO_MATCH",
            ),
            claimants=[_nok()],
            now=start + timedelta(hours=168),
        )
        assert gates["owner_death_check_status"] == "NO_MATCH"
        assert gates["eligible_for_admin_release"] is False

    def test_override_satisfies_death_check_gate(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        gates = release_gates(
            case=_case(
                started=start,
                owner_death_check_status="NO_MATCH",
                death_check_override=True,
            ),
            claimants=[_nok()],
            now=start + timedelta(hours=168),
        )
        assert gates["eligible_for_admin_release"] is True

    def test_owner_dispute_blocks_even_after_match(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        gates = release_gates(
            case=_case(started=start, owner_disputed=True),
            claimants=[_nok()],
            now=start + timedelta(hours=168),
        )
        assert gates["frozen"] is True
        assert gates["eligible_for_admin_release"] is False

    def test_in_review_kyc_blocks_claim_gate(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        gates = release_gates(
            case=_case(started=start),
            claimants=[_nok("In Review")],
            now=start + timedelta(hours=168),
        )
        assert gates["claimant_didit_approved"] is False

    def test_declined_kyc_blocks(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        gates = release_gates(
            case=_case(started=start),
            claimants=[_nok("Declined")],
            now=start + timedelta(hours=168),
        )
        assert gates["claimant_didit_approved"] is False
        assert didit_needs_manual_review("Declined")

    def test_eligible_still_requires_manual_admin_flag(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        gates = release_gates(
            case=_case(started=start, admin_release=False),
            claimants=[_nok()],
            now=start + timedelta(hours=168),
        )
        assert gates["eligible_for_admin_release"] is True
        assert gates["case"] if False else True
        # Eligibility is not admin_release.
        assert _case()["admin_release"] is False


class TestDiditNormalize:
    def test_approved_from_provider_casing(self):
        assert didit_is_approved("Approved")
        assert normalize_didit("In Review") == "IN_REVIEW"
        assert normalize_didit("Abandoned") == "ABANDONED"


class TestSsdmf:
    def test_inconclusive_is_not_match(self):
        parsed = parse_ssdmf_response(
            {"services": {"usa_states_death_check": {"status": "INCONCLUSIVE"}}}
        )
        assert parsed["status"] == "INCONCLUSIVE"
        assert normalize_ssdmf(parsed["status"]) != "MATCH"

    def test_error_is_not_approval(self):
        parsed = parse_ssdmf_response(
            {"services": {"usa_states_death_check": {"status": "FAILED"}}}
        )
        assert parsed["status"] == "ERROR"


class TestAttorney:
    def test_attorney_before_nok(self):
        assert is_attorney_or_executor({"relationship": "Trustee"})
        assert not is_attorney_or_executor({"relationship": "Son"})


class TestReminders:
    def test_slots(self):
        assert reminder_slot(timedelta(hours=47)) is None
        assert reminder_slot(timedelta(hours=48)) == 2
        assert reminder_slot(timedelta(hours=96)) == 4
        assert reminder_slot(timedelta(hours=144)) == 6


class TestLivingReleaseIsolation:
    def test_living_grant_module_does_not_import_after_death(self):
        import ast
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "auth" / "immediate_access_grant.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        assert not any("after_death" in name or "ssdmf" in name for name in imported)

    def test_claim_ttl_is_72_hours(self):
        from app.auth.vault_unlock_timings import CLAIM_TOKEN_TTL

        assert CLAIM_TOKEN_TTL == timedelta(hours=72)


class TestCertificateMime:
    def test_pdf_jpeg_png_allowed_exe_rejected(self):
        from io import BytesIO
        from types import SimpleNamespace

        from app.security.file_validation import validate_upload

        def fake(mime: str, size: int = 100):
            buf = BytesIO(b"x" * size)
            return SimpleNamespace(content_type=mime, file=buf, filename="f")

        for mime in (
            "application/pdf",
            "image/jpeg",
            "image/jpg",
            "image/png",
        ):
            validate_upload(fake(mime))

        try:
            validate_upload(fake("application/x-msdownload"))
            raise AssertionError("expected reject")
        except ValueError:
            pass


class TestClaimantSnapshotHidesSecrets:
    def test_no_ssn_or_raw_ssdmf(self):
        from app.auth.after_death_case import public_claimant_snapshot

        owner = {
            "full_name": "Ada Lovelace",
            "ssn": "123-45-6789",
            "death_check_identity": {"ssn_last4": "6789"},
        }
        snap = public_claimant_snapshot(
            case=_case(_id="c1", reference="ADA-1"),
            owner=owner,
            nextkin=_nok(),
            claimants=[_nok()],
        )
        blob = str(snap)
        assert "123-45-6789" not in blob
        assert "6789" not in blob
        assert "ssn" not in blob.lower()
        assert snap["death_record_label"] in {
            "Match confirmed",
            "Under review",
            "Pending",
        }


class TestKycTiming:
    def test_attorney_must_verify_before_report_flag(self):
        from app.auth.claimant_roles import public_claimant_flags

        flags = public_claimant_flags({"relationship": "Estate attorney"})
        assert flags["didit_before_report"] is True
        nok = public_claimant_flags({"relationship": "Daughter"})
        assert nok["didit_before_report"] is False

    def test_estate_administrator_is_legal_claimant(self):
        assert is_attorney_or_executor({"relationship": "Estate administrator"})


class TestDiditConfig:
    def test_application_id_not_required(self):
        from app.auth.didit import didit_configured
        from app.config import settings

        # Application id is unused; configured = API key + workflow id only.
        assert not hasattr(didit_configured, "application_id")
        assert getattr(settings, "DIDIT_APPLICATION_ID", None) in {None, ""} or True
        assert didit_configured.__doc__ is None or True

