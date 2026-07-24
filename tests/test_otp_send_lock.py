"""Backend unit tests — OTP send lock prevents duplicate deliveries."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth import otp_security


class _FakeUpdateResult:
    def __init__(self, matched_count: int):
        self.matched_count = matched_count


@pytest.mark.asyncio
async def test_claim_otp_send_slot_allows_first_then_blocks_second():
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=_FakeUpdateResult(0))
    collection.insert_one = AsyncMock(side_effect=[None, Exception("dup")])
    collection.find_one = AsyncMock(
        return_value={
            "key": "sms:+15551212",
            "lockedUntil": datetime.utcnow() + timedelta(seconds=45),
        }
    )
    collection.create_index = AsyncMock()

    # Force DuplicateKeyError path on second insert
    from pymongo.errors import DuplicateKeyError

    collection.insert_one = AsyncMock(
        side_effect=[None, DuplicateKeyError("dup")]
    )

    with patch.object(otp_security, "otp_send_locks_collection", collection):
        otp_security._otp_send_lock_index_ready = True
        claimed1, _ = await otp_security.claim_otp_send_slot(
            channel="sms",
            destination="+15551212",
            cooldown_seconds=45,
        )
        claimed2, remaining = await otp_security.claim_otp_send_slot(
            channel="sms",
            destination="+15551212",
            cooldown_seconds=45,
        )

    assert claimed1 is True
    assert claimed2 is False
    assert remaining >= 1


@pytest.mark.asyncio
async def test_release_otp_send_slot_deletes_lock():
    collection = MagicMock()
    collection.delete_one = AsyncMock()

    with patch.object(otp_security, "otp_send_locks_collection", collection):
        await otp_security.release_otp_send_slot(
            channel="email",
            destination="user@example.com",
        )

    collection.delete_one.assert_awaited_once()
