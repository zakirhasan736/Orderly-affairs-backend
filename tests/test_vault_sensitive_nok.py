from app.auth.vault_privacy import strip_hidden_fields_for_nok
from app.auth.vault_sensitive_fields import last_four_digits


def test_nok_bank_payload_is_last_four_only():
    data = {
        "12A": [
            {
                "bank_name": "Chase",
                "account_number": "123456789012",
                "routing_number": "021000021",
                "account_documents": {"files": [{"url": "x"}]},
            }
        ]
    }
    out = strip_hidden_fields_for_nok(data, {"rules": []}, "12")
    row = out["12A"][0]
    assert row["bank_name"] == "Chase"
    assert row["account_number"] == "9012"
    assert "routing_number" not in row
    assert "account_documents" not in row


def test_nok_policy_and_password_projection():
    insurance = strip_hidden_fields_for_nok(
        {"7A": {"carrier": "Geico", "policy_number": "AA11223344", "policy_documents": {"files": [1]}}},
        {"rules": []},
        "7",
    )
    assert insurance["7A"]["carrier"] == "Geico"
    assert insurance["7A"]["policy_number"] == "3344"
    assert "policy_documents" not in insurance["7A"]

    vital = strip_hidden_fields_for_nok(
        {"1A": {"full_legal_name": "Ada", "primary_email_password": "x"}},
        {"rules": []},
        "1",
    )
    assert vital["1A"]["full_legal_name"] == "Ada"
    assert "primary_email_password" not in vital["1A"]


def test_last_four_from_wrapped_text():
    assert last_four_digits({"text": "xxxx-4412"}) == "4412"
