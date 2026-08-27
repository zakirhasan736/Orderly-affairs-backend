"""Death certificate authorization helpers."""

from app.legal.death_certificate_authorization import (
    VERSION,
    agreement_set_fields,
    owner_has_death_certificate_authorization,
)


class TestDeathCertificateAuthorization:
    def test_unsigned_owner_is_not_agreed(self):
        assert owner_has_death_certificate_authorization({}) is False
        assert owner_has_death_certificate_authorization(None) is False

    def test_signed_current_version_counts(self):
        owner = {
            "death_certificate_authorization": {
                "version": VERSION,
                "agreed": True,
                "signature_name": "Jane Doe",
            }
        }
        assert owner_has_death_certificate_authorization(owner) is True

    def test_old_version_requires_reagree(self):
        owner = {
            "death_certificate_authorization": {
                "version": "2000-01-01",
                "agreed": True,
                "signature_name": "Jane Doe",
            }
        }
        assert owner_has_death_certificate_authorization(owner) is False

    def test_set_fields_include_signature(self):
        fields = agreement_set_fields("  Jane Doe  ")
        rec = fields["death_certificate_authorization"]
        assert rec["agreed"] is True
        assert rec["signature_name"] == "Jane Doe"
        assert rec["version"] == VERSION
