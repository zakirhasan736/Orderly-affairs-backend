"""Unit tests for expiry / deadline reminder field detection."""

from datetime import datetime, timedelta

from app.notifications.section_expiry_scheduler import (
    collect_expiry_events,
    parse_expiry_date,
)


def test_parse_common_date_formats():
    assert parse_expiry_date("2026-12-31").date().isoformat() == "2026-12-31"
    assert parse_expiry_date("12/31/2026").month == 12
    assert parse_expiry_date("15") is None  # day-of-month only


def test_collects_insurance_and_renewal_deadlines():
    in_ten = (datetime.utcnow() + timedelta(days=10)).strftime("%Y-%m-%d")
    data = {
        "7A": [
            {
                "policy_company": "Acme Ins",
                "policy_type": "Auto",
                "policy_expiry": in_ten,
            }
        ],
        "6A": {
            "mortgage_maturity_date": in_ten,
            "property_tax_due_date": in_ten,
            "lease_end_date": in_ten,
        },
        "16B": [
            {
                "creditor_name": "Bank",
                "payment_due_date": "15",
                "next_payment_due_date": in_ten,
                "loan_maturity_date": in_ten,
            }
        ],
        "20B": {"tax_filing_deadline": in_ten},
        "20C": [{"document_type": "Contract", "expiration_date": in_ten}],
        "8A": [{"organization_name": "Club", "renewal_date": in_ten}],
        "19B": [
            {
                "property_address": "1 Main",
                "mortgage_maturity_date": in_ten,
                "property_tax_due_date": in_ten,
            }
        ],
    }

    events = collect_expiry_events("7", {"7A": data["7A"]})
    assert any(e["field_key"] == "policy_expiry" for e in events)

    events6 = collect_expiry_events("6", {"6A": data["6A"]})
    keys6 = {e["field_key"] for e in events6}
    assert "mortgage_maturity_date" in keys6
    assert "property_tax_due_date" in keys6
    assert "lease_end_date" in keys6

    events16 = collect_expiry_events("16", {"16B": data["16B"]})
    keys16 = {e["field_key"] for e in events16}
    assert "next_payment_due_date" in keys16
    assert "loan_maturity_date" in keys16
    assert "payment_due_date" not in keys16  # day-of-month skipped

    events20 = collect_expiry_events(
        "20", {"20B": data["20B"], "20C": data["20C"]}
    )
    keys20 = {e["field_key"] for e in events20}
    assert "tax_filing_deadline" in keys20
    assert "expiration_date" in keys20

    events8 = collect_expiry_events("8", {"8A": data["8A"]})
    assert any(e["field_key"] == "renewal_date" for e in events8)

    events19 = collect_expiry_events("19", {"19B": data["19B"]})
    keys19 = {e["field_key"] for e in events19}
    assert "mortgage_maturity_date" in keys19
    assert "property_tax_due_date" in keys19


def test_skips_renewal_requirements_text():
    events = collect_expiry_events(
        "20",
        {
            "20C": [
                {
                    "renewal_requirements": "Must renew annually with paperwork",
                    "expiration_date": "2030-01-01",
                }
            ]
        },
    )
    assert all(e["field_key"] != "renewal_requirements" for e in events)
    assert any(e["field_key"] == "expiration_date" for e in events)
