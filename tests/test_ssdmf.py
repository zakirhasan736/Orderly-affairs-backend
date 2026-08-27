from app.auth.claimant_roles import is_attorney_or_executor
from app.auth.ssdmf import (
    last4_from_ssn,
    parse_ssdmf_response,
    release_blockers,
    snapshot_death_check_identity,
    SSDMF_MATCH,
    SSDMF_NO_MATCH,
)


class TestSsdmfOwnerIdentity:
    def test_match_full_match(self):
        parsed = parse_ssdmf_response(
            {
                "id": "req-1",
                "services": {
                    "usa_states_death_check": {
                        "status": "MATCH",
                        "full_match": True,
                    }
                },
            }
        )
        assert parsed["status"] == SSDMF_MATCH
        assert parsed["full_match"] is True
        assert parsed["vendor_id"] == "req-1"

    def test_no_match_is_not_alive_proof(self):
        parsed = parse_ssdmf_response(
            {"services": {"usa_states_death_check": {"status": "NO_MATCH"}}}
        )
        assert parsed["status"] == SSDMF_NO_MATCH
        assert parsed["full_match"] is False

    def test_last4_never_keeps_full_ssn(self):
        assert last4_from_ssn("123-45-6789") == "6789"
        assert last4_from_ssn("***-**-0042") == "0042"
        assert last4_from_ssn("81") == ""

    def test_snapshot_is_owner_not_nok(self):
        snap = snapshot_death_check_identity(
            {
                "full_legal_name": "Ada Lovelace",
                "date_of_birth": "1815-12-10",
                "social_security_number": "6781",
            },
            full_name_fallback="Ignored",
        )
        assert snap["first_name"] == "Ada"
        assert snap["last_name"] == "Lovelace"
        assert snap["ssn_last4"] == "6781"
        assert "Jane Reporter" not in str(snap)

    def test_release_requires_certificate_or_override(self):
        blocked = release_blockers(
            {},
            ssdmf_override=False,
            certificate_override=False,
        )
        assert blocked is not None
        assert blocked["requires_certificate_override"] is True

        allowed = release_blockers(
            {},
            ssdmf_override=False,
            certificate_override=True,
        )
        assert allowed is None

    def test_no_match_blocks_without_override(self):
        owner = {
            "death_verification": {
                "certificate": {"uploaded_at": "2026-08-27"},
                "ssdmf": {"status": SSDMF_NO_MATCH, "full_match": False},
            }
        }
        blocked = release_blockers(
            owner,
            ssdmf_override=False,
            certificate_override=False,
        )
        assert blocked is not None
        assert blocked["requires_ssdmf_override"] is True
        allowed = release_blockers(
            owner,
            ssdmf_override=True,
            certificate_override=False,
        )
        assert allowed is None

    def test_match_allows_release_after_wait(self):
        owner = {
            "death_verification": {
                "certificate": {"uploaded_at": "2026-08-27"},
                "ssdmf": {"status": SSDMF_MATCH, "full_match": True},
            },
            "owner_wait_elapsed": True,
        }
        assert (
            release_blockers(
                owner,
                ssdmf_override=False,
                certificate_override=False,
            )
            is None
        )

    def test_open_wait_blocks_without_override(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        owner = {
            "death_verification": {
                "certificate": {"uploaded_at": now},
                "ssdmf": {"status": SSDMF_MATCH, "full_match": True},
            },
            "owner_wait_started_at": now,
            "owner_wait_ends_at": now + timedelta(days=7),
            "owner_wait_elapsed": False,
        }
        blocked = release_blockers(
            owner,
            ssdmf_override=False,
            certificate_override=False,
        )
        assert blocked is not None
        assert blocked["requires_wait_override"] is True
        allowed = release_blockers(
            owner,
            ssdmf_override=False,
            certificate_override=False,
            wait_override=True,
        )
        assert allowed is None


class TestAttorneyClaimant:
    def test_attorney_relationship(self):
        assert is_attorney_or_executor({"relationship": "Estate attorney"})
        assert is_attorney_or_executor({"relationship": "Executor"})
        assert not is_attorney_or_executor({"relationship": "Daughter"})
        assert not is_attorney_or_executor(
            {"relationship": "Attorney", "access_type": "family"}
        )
