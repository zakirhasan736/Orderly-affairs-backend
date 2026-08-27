from datetime import datetime, timedelta

from app.auth.immediate_access_grant import (
    IMMEDIATE_ACCESS_EMAIL_DELAY,
    LIVING_CREDENTIAL_TTL,
    pending_immediate_access_filter,
)


from app.auth.living_release_lock import RELEASE_LOCKOUT, RELEASE_MAX_FAILURES
from app.auth.vault_unlock_timings import (
    ADMIN_RELEASE_ESCALATE_AFTER,
    MANUAL_REVIEW_FOLLOW_UP,
    OWNER_CERTIFICATE_WAIT,
    OWNER_WAIT_REMINDER_EVERY,
    OWNER_WINDOW_WHEN_DOCUMENT_AND_MORTALITY_AGREE,
)


class TestImmediateAccessDelay:
    def test_delay_is_immediate(self):
        assert IMMEDIATE_ACCESS_EMAIL_DELAY == timedelta(minutes=0)

    def test_credential_window_is_seven_days(self):
        assert LIVING_CREDENTIAL_TTL == timedelta(days=7)

    def test_release_lockout_is_five_fails_then_fifteen_minutes(self):
        assert RELEASE_MAX_FAILURES == 5
        assert RELEASE_LOCKOUT == timedelta(minutes=15)

    def test_due_filter_requires_pending_and_not_live(self):
        now = datetime(2026, 8, 19, 12, 0, 0)
        query = pending_immediate_access_filter(now=now)
        assert query["role"] == "nextkin"
        assert query["immediate_access_pending"] is True
        assert query["immediate_access"] == {"$ne": True}
        assert query["access_revoked"] == {"$ne": True}
        assert query["immediate_access_email_at"] == {"$lte": now}

    def test_rev9_unlock_timings(self):
        assert OWNER_WINDOW_WHEN_DOCUMENT_AND_MORTALITY_AGREE == timedelta(hours=24)
        assert MANUAL_REVIEW_FOLLOW_UP == timedelta(hours=48)
        assert ADMIN_RELEASE_ESCALATE_AFTER == timedelta(hours=24)
        assert OWNER_CERTIFICATE_WAIT == timedelta(days=7)
        assert OWNER_WAIT_REMINDER_EVERY == timedelta(days=2)
