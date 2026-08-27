from app.notifications.email_layout import brand_logo_url


def test_brand_logo_url_uses_public_frontend(monkeypatch):
    monkeypatch.setattr(
        "app.notifications.email_layout.settings.FRONTEND_URL",
        "https://vault.orderly-affairs.com",
    )
    monkeypatch.setattr(
        "app.notifications.email_layout.settings.EMAIL_LOGO_URL",
        None,
    )
    assert brand_logo_url() == "https://vault.orderly-affairs.com/images/brand-logo.png"


def test_brand_logo_url_skips_localhost(monkeypatch):
    monkeypatch.setattr(
        "app.notifications.email_layout.settings.FRONTEND_URL",
        "http://localhost:3000",
    )
    monkeypatch.setattr(
        "app.notifications.email_layout.settings.EMAIL_LOGO_URL",
        None,
    )
    assert brand_logo_url() == "https://vault.orderly-affairs.com/images/brand-logo.png"


def test_brand_logo_url_ignores_legacy_cloudinary(monkeypatch):
    monkeypatch.setattr(
        "app.notifications.email_layout.settings.FRONTEND_URL",
        "https://vault.orderly-affairs.com",
    )
    monkeypatch.setattr(
        "app.notifications.email_layout.settings.EMAIL_LOGO_URL",
        "https://res.cloudinary.com/davvdgwe3/image/upload/v1/orderly-affairs/brand-logo.png",
    )
    assert brand_logo_url() == "https://vault.orderly-affairs.com/images/brand-logo.png"


def test_brand_logo_url_respects_override(monkeypatch):
    monkeypatch.setattr(
        "app.notifications.email_layout.settings.EMAIL_LOGO_URL",
        "https://cdn.example.com/oa-logo.png",
    )
    assert brand_logo_url() == "https://cdn.example.com/oa-logo.png"
