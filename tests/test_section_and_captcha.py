"""Backend unit tests — section empty-data helpers and captcha gates."""

from unittest.mock import patch

from app.auth.captcha import verify_captcha_token
from app.utils.empty import is_effectively_empty


class TestSectionEmptyDetection:
    def test_none_and_empty_containers(self):
        assert is_effectively_empty(None) is True
        assert is_effectively_empty("") is True
        assert is_effectively_empty([]) is True
        assert is_effectively_empty({}) is True

    def test_nested_empty_dicts(self):
        assert is_effectively_empty({"a": "", "b": {"c": None}}) is True
        assert is_effectively_empty({"a": "value"}) is False

    def test_list_of_empty_values(self):
        assert is_effectively_empty([{}, "", None]) is True
        assert is_effectively_empty(["x"]) is False


class TestCaptchaGate:
    def test_disabled_captcha_always_passes(self):
        with patch("app.auth.captcha.settings") as settings:
            settings.OTP_CAPTCHA_ENABLED = False
            settings.APP_ENV = "production"
            settings.TURNSTILE_SECRET_KEY = "secret"
            assert verify_captcha_token(None) is True
            assert verify_captcha_token("") is True

    def test_missing_token_fails_when_enabled(self):
        with patch("app.auth.captcha.settings") as settings:
            settings.OTP_CAPTCHA_ENABLED = True
            settings.APP_ENV = "production"
            settings.TURNSTILE_SECRET_KEY = "secret"
            assert verify_captcha_token(None) is False
            assert verify_captcha_token("  ") is False
            assert verify_captcha_token("captcha-disabled") is False

    def test_dev_bypass_tokens(self):
        with patch("app.auth.captcha.settings") as settings:
            settings.OTP_CAPTCHA_ENABLED = True
            settings.APP_ENV = "development"
            settings.TURNSTILE_SECRET_KEY = "secret"
            assert verify_captcha_token("dev-bypass") is True

    def test_successful_token_can_be_reused_briefly(self):
        import app.auth.captcha as captcha_mod

        captcha_mod._VERIFIED_TOKENS.clear()
        token = "turnstile-token-abc"

        with patch("app.auth.captcha.settings") as settings, patch(
            "app.auth.captcha.urllib.request.urlopen"
        ) as urlopen:
            settings.OTP_CAPTCHA_ENABLED = True
            settings.APP_ENV = "production"
            settings.TURNSTILE_SECRET_KEY = "secret"

            class _Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self):
                    return b'{"success": true}'

            urlopen.return_value = _Resp()
            assert verify_captcha_token(token) is True
            urlopen.side_effect = AssertionError("siteverify should be skipped")
            assert verify_captcha_token(token) is True

    def test_cached_token_skips_cloudflare(self):
        import app.auth.captcha as captcha_mod

        captcha_mod._VERIFIED_TOKENS.clear()
        token = "turnstile-token-dup"
        captcha_mod._remember_verified_token(token)

        with patch("app.auth.captcha.settings") as settings, patch(
            "app.auth.captcha.urllib.request.urlopen"
        ) as urlopen:
            settings.OTP_CAPTCHA_ENABLED = True
            settings.APP_ENV = "production"
            settings.TURNSTILE_SECRET_KEY = "secret"

            assert verify_captcha_token(token) is True
            urlopen.assert_not_called()
