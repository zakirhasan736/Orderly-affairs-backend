from datetime import timedelta

from app.auth.didit import (
    DIDIT_APPROVED,
    shorten_floats,
    verify_webhook_signature,
)
from app.auth.vault_unlock_timings import CLAIM_TOKEN_TTL


class TestDiditWebhookSignature:
    def test_v2_accepts_canonical_body(self):
        body = {
            "webhook_type": "status.updated",
            "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "status": DIDIT_APPROVED,
            "timestamp": 1774970000,
            "vendor_data": "user_42",
        }
        import hashlib
        import hmac
        import json

        secret = "test-secret"
        canonical = json.dumps(
            shorten_floats(body),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        sig = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
        assert verify_webhook_signature(
            body=body,
            signature_v2=sig,
            signature_simple=None,
            signature_raw=None,
            timestamp_header=str(int(__import__("time").time())),
            raw_body=b"{}",
            secret=secret,
        )

    def test_rejects_bad_signature(self):
        body = {"webhook_type": "status.updated", "status": "Approved", "timestamp": 1}
        assert (
            verify_webhook_signature(
                body=body,
                signature_v2="deadbeef",
                signature_simple=None,
                signature_raw=None,
                timestamp_header=str(int(__import__("time").time())),
                raw_body=b"{}",
                secret="test-secret",
            )
            is False
        )

    def test_claim_ttl_is_72_hours(self):
        assert CLAIM_TOKEN_TTL == timedelta(hours=72)
