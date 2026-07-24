"""Backend unit tests — Access Management / empty-field validation."""

from app.auth.nextkin_schemas import NextKinCreateRequest
from app.auth.nextkin_validation import (
    FULL_KIT_ACCESS,
    SECTION_SPECIFIC_ACCESS,
    normalize_access_level,
    prepare_nextkin_create_fields,
    validate_nextkin_required_fields,
)
from app.security.access_control import assert_section_read_access
from fastapi import HTTPException
from pydantic import ValidationError
import pytest


class TestNextKinFieldValidation:
    def test_rejects_blank_full_name(self):
        err = validate_nextkin_required_fields(
            full_name="   ",
            email="a@b.com",
            relationship="Spouse",
            require_password=False,
        )
        assert err == "Full name is required"

    def test_rejects_blank_relationship(self):
        err = validate_nextkin_required_fields(
            full_name="Jane",
            email="a@b.com",
            relationship="",
            require_password=False,
        )
        assert err == "Relationship is required"

    def test_rejects_section_specific_without_sections(self):
        err = validate_nextkin_required_fields(
            full_name="Jane",
            email="a@b.com",
            relationship="Sister",
            access_level=SECTION_SPECIFIC_ACCESS,
            authorized_sections=[],
            require_password=False,
        )
        assert "section" in (err or "").lower()

    def test_accepts_complete_payload(self):
        err = validate_nextkin_required_fields(
            full_name="Jane",
            email="a@b.com",
            relationship="Sister",
            access_level=FULL_KIT_ACCESS,
            master_password="Secret123!",
            require_password=True,
        )
        assert err is None

    def test_normalize_access_level_aliases(self):
        assert normalize_access_level("full") == FULL_KIT_ACCESS
        assert normalize_access_level("limited") == SECTION_SPECIFIC_ACCESS
        assert normalize_access_level(SECTION_SPECIFIC_ACCESS) == SECTION_SPECIFIC_ACCESS

    def test_prepare_raises_on_empty_name(self):
        from types import SimpleNamespace

        payload = SimpleNamespace(
            email="jane@example.com",
            full_name="   ",
            relationship="Friend",
            access_level=SECTION_SPECIFIC_ACCESS,
            authorized_sections=[],
            master_password=None,
        )
        with pytest.raises(ValueError, match="Full name"):
            prepare_nextkin_create_fields(payload)

    def test_pydantic_rejects_blank_full_name_on_create(self):
        with pytest.raises(ValidationError):
            NextKinCreateRequest(
                email="jane@example.com",
                full_name="  ",
                relationship="Friend",
            )

    def test_pydantic_rejects_blank_relationship_on_create(self):
        with pytest.raises(ValidationError):
            NextKinCreateRequest(
                email="jane@example.com",
                full_name="Jane",
                relationship=" ",
            )


class TestAccessControl:
    def test_owner_always_allowed(self):
        assert_section_read_access({"role": "owner"}, "1")

    def test_nextkin_requires_approval(self):
        with pytest.raises(HTTPException) as exc:
            assert_section_read_access(
                {
                    "role": "nextkin",
                    "immediate_access": False,
                    "access_level": FULL_KIT_ACCESS,
                },
                "1",
            )
        assert exc.value.status_code == 403

    def test_full_kit_access_allows_any_section(self):
        assert_section_read_access(
            {
                "role": "nextkin",
                "immediate_access": True,
                "access_level": FULL_KIT_ACCESS,
            },
            "7",
        )

    def test_section_specific_denies_unlisted(self):
        with pytest.raises(HTTPException) as exc:
            assert_section_read_access(
                {
                    "role": "nextkin",
                    "immediate_access": True,
                    "access_level": SECTION_SPECIFIC_ACCESS,
                    "authorized_sections": ["1"],
                },
                "7",
            )
        assert exc.value.status_code == 403

    def test_section_specific_allows_listed(self):
        assert_section_read_access(
            {
                "role": "nextkin",
                "immediate_access": True,
                "access_level": SECTION_SPECIFIC_ACCESS,
                "authorized_sections": ["7"],
            },
            "7",
        )
