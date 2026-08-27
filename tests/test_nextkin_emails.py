from app.notifications.nextkin_emails import render_nok_invite_email


def test_nok_invite_shows_next_of_kin_not_viewer():
    html = render_nok_invite_email(
        owner_name="Sebastian Shahvandi",
        recipient_name="SebastianNOK",
        plain_password=None,
        login_url="https://example.com/nextkin-login",
        pending_approval=False,
        access_timing="upon_death",
        portal_role_label="Viewer",
        access_summary="Full kit access",
    )
    assert "Your access" in html
    assert "Next of Kin" in html
    assert "Full kit access" in html
    assert "Viewer" not in html
    assert "/images/brand-logo.png" in html
