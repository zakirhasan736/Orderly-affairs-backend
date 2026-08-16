from datetime import date

from app.auth.notification_prefs import (
    merge_notification_prefs_patch,
    normalize_notification_prefs,
    resolve_section_update_recipient_ids,
)
from app.notifications.special_day_scheduler import (
    collect_special_days_from_vault,
    days_until_month_day,
    merge_special_days,
)


def test_default_section_recipients_are_everyone():
    prefs = normalize_notification_prefs({})
    assert resolve_section_update_recipient_ids(prefs, "7") is None


def test_per_section_override_wins():
    prefs = merge_notification_prefs_patch(
        {},
        {
            "section_update_recipient_ids": ["global-1"],
            "section_update_recipients_by_section": {"7": ["alice", "bob"]},
        },
    )
    assert resolve_section_update_recipient_ids(prefs, "7") == ["alice", "bob"]
    assert resolve_section_update_recipient_ids(prefs, "5") == ["global-1"]


def test_clear_section_override_restores_default():
    prefs = merge_notification_prefs_patch(
        {"section_update_recipients_by_section": {"7": ["alice"]}},
        {"section_update_recipients_by_section": {"7": None}},
    )
    assert "7" not in prefs["section_update_recipients_by_section"]
    assert resolve_section_update_recipient_ids(prefs, "7") is None


def test_collects_birthday_and_anniversary_from_vault():
    days = collect_special_days_from_vault(
        {
            "vital_info": {
                "date_of_birth": "1978-09-15",
                "wedding_date": "2004-06-12",
            }
        }
    )
    kinds = {(item["kind"], item["month"], item["day"]) for item in days}
    assert ("birthday", 9, 15) in kinds
    assert ("anniversary", 6, 12) in kinds


def test_disabled_pref_blocks_vault_duplicate():
    merged = merge_special_days(
        [
            {
                "kind": "birthday",
                "month": 9,
                "day": 15,
                "label": "Birthday",
                "enabled": False,
            }
        ],
        [
            {
                "kind": "birthday",
                "month": 9,
                "day": 15,
                "label": "Birthday",
                "enabled": True,
                "source": "vault",
            }
        ],
    )
    assert merged == []


def test_days_until_handles_year_wrap():
    assert days_until_month_day(1, 1, date(2026, 12, 25)) == 7
    assert days_until_month_day(8, 16, date(2026, 8, 16)) == 0
