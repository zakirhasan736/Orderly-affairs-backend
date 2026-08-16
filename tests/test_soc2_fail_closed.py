from app.config import Settings


def test_audit_retention_meets_twelve_months():
    s = Settings.model_construct(
        APP_ENV="production",
        VAULT_AUDIT_RETENTION_DAYS=400,
        CLAMD_REQUIRED=False,
        ADMIN_ALLOW_OWNER_COOKIE_FALLBACK=False,
    )
    assert s.VAULT_AUDIT_RETENTION_DAYS >= 365
    assert s.clamd_is_required is True
    assert s.allow_owner_cookie_admin_fallback is False


def test_dev_does_not_force_clamav_unless_flagged():
    s = Settings.model_construct(
        APP_ENV="development",
        CLAMD_REQUIRED=False,
        ADMIN_ALLOW_OWNER_COOKIE_FALLBACK=None,
    )
    assert s.clamd_is_required is False
    assert s.allow_owner_cookie_admin_fallback is True
