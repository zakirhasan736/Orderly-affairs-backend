"""Backend unit tests — auth phone, OTP storage, passwords, rate-limit steps."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.auth.phone import (
    format_phone,
    looks_like_email,
    looks_like_phone_identifier,
)
from app.security.auth_rate_limit import _as_naive_utc, _lock_duration_for_level
from app.security.error_handlers import (
    GENERIC_NOT_FOUND,
    GENERIC_SERVER_ERROR,
    GENERIC_VALIDATION_ERROR,
    _sanitize_http_detail,
)
from app.security.otp_storage import (
    hash_otp_value,
    otp_storage_fields,
    verify_stored_otp,
)
from app.security.password_handler import hash_password, verify_password


class TestPhoneFormatting:
    def test_requires_phone(self):
        with pytest.raises(ValueError, match="required"):
            format_phone("   ")

    def test_formats_us_national_and_e164(self):
        assert format_phone("2025550123") == "+12025550123"
        assert format_phone("+12025550123") == "+12025550123"

    def test_rejects_invalid(self):
        with pytest.raises(ValueError):
            format_phone("123")

    def test_login_identifier_helpers(self):
        assert looks_like_email("a@b.com") is True
        assert looks_like_email("+12025550123") is False
        assert looks_like_phone_identifier("+12025550123") is True
        assert looks_like_phone_identifier("(202) 555-0123") is True
        assert looks_like_phone_identifier("a@b.com") is False
        assert looks_like_phone_identifier("123") is False


class TestOtpStorage:
    def test_hash_is_deterministic_and_scoped(self):
        a = hash_otp_value("User@Example.com", 123456, "login")
        b = hash_otp_value("user@example.com", 123456, "login")
        c = hash_otp_value("user@example.com", 123456, "reset")
        assert a == b
        assert a != c

    def test_storage_fields_lowercases_email(self):
        fields = otp_storage_fields("USER@Example.com", 111222, "mfa")
        assert fields["email"] == "user@example.com"
        assert fields["otp_hash"]
        assert fields["type"] == "mfa"

    def test_verify_hash_and_legacy_plaintext(self):
        fields = otp_storage_fields("a@b.com", 555666, "login")
        assert verify_stored_otp(fields, 555666) is True
        assert verify_stored_otp(fields, 999999) is False
        assert verify_stored_otp({"email": "a@b.com", "otp": "424242"}, "424242") is True
        assert verify_stored_otp({}, 1) is False


class TestPasswordHandler:
    def test_hash_and_verify_roundtrip(self):
        hashed = hash_password("SecurePass123!")
        assert hashed != "SecurePass123!"
        assert verify_password("SecurePass123!", hashed) is True
        assert verify_password("wrong", hashed) is False
        assert verify_password("anything", "") is False


class TestAuthRateLimitHelpers:
    def test_lock_duration_steps(self):
        assert _lock_duration_for_level(0) == 45
        assert _lock_duration_for_level(1) == 300
        assert _lock_duration_for_level(2) == 900
        assert _lock_duration_for_level(99) <= 1800

    def test_as_naive_utc_strips_tz(self):
        aware = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        naive = _as_naive_utc(aware)
        assert naive is not None
        assert naive.tzinfo is None
        assert _as_naive_utc(None) is None


class TestErrorHandlers:
    def test_non_production_returns_raw_detail(self):
        with patch("app.security.error_handlers._is_production", return_value=False):
            assert _sanitize_http_detail(500, "boom") == "boom"

    def test_production_masks_server_and_not_found(self):
        with patch("app.security.error_handlers._is_production", return_value=True):
            assert _sanitize_http_detail(500, "secret stack") == GENERIC_SERVER_ERROR
            assert _sanitize_http_detail(404, "missing row") == GENERIC_NOT_FOUND
            assert _sanitize_http_detail(400, "Email already used") == "Email already used"
            assert _sanitize_http_detail(422, [{"loc": ["body"]}]) == GENERIC_VALIDATION_ERROR
            assert _sanitize_http_detail(400, {"msg": "x"}) == {
                "message": GENERIC_VALIDATION_ERROR
            }


class TestCaptchaExtra:
    def test_missing_secret_fails_when_enabled(self):
        from app.auth.captcha import verify_captcha_token

        with patch("app.auth.captcha.settings") as settings:
            settings.OTP_CAPTCHA_ENABLED = True
            settings.APP_ENV = "production"
            settings.TURNSTILE_SECRET_KEY = ""
            assert verify_captcha_token("token-abc") is False
