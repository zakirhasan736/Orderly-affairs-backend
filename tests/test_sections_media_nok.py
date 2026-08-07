"""Backend unit tests — section schemas, media helpers, file validation, NOK access."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.auth.nextkin_schemas import NextKinCreateRequest
from app.auth.nextkin_validation import (
    FULL_KIT_ACCESS,
    SECTION_SPECIFIC_ACCESS,
    normalize_access_level,
    validate_nextkin_required_fields,
)
from app.security.access_control import assert_section_read_access
from app.security.cloudinary_service import (
    _normalize_resource_type,
    validate_message_media_size,
)
from app.security.file_validation import validate_upload
from app.sections.section6_main_residence.schemas import UploadField, Section6AData
from app.utils.empty import is_effectively_empty


class TestSection6UploadField:
    def test_coerce_string_to_upload_field(self):
        field = UploadField.model_validate("deed note")
        assert field.text == "deed note"
        assert field.files == []

    def test_coerce_deleted_files_alias(self):
        field = UploadField.model_validate(
            {"text": "x", "files": [], "_deleted_files": ["old"]}
        )
        assert field.deleted_files == ["old"]

    def test_section6a_accepts_string_upload_keys(self):
        data = Section6AData.model_validate(
            {
                "home_address": "123 Main",
                "property_deeds_titles": "Scanned deed",
            }
        )
        assert data.home_address == "123 Main"
        assert data.property_deeds_titles is not None
        assert data.property_deeds_titles.text == "Scanned deed"


class TestMediaAndUploads:
    def test_message_media_size_limit(self):
        validate_message_media_size(1000)
        # MESSAGE_MEDIA_MAX_BYTES=0 → no app-level cap
        validate_message_media_size(151 * 1024 * 1024)
        with pytest.raises(ValueError, match="empty"):
            validate_message_media_size(0)

    def test_normalize_resource_type(self):
        assert _normalize_resource_type("audio") == "video"
        assert _normalize_resource_type("image") == "image"
        assert _normalize_resource_type("auto") is None
        assert _normalize_resource_type("weird") is None

    def test_validate_upload_mime_and_size(self):
        ok = SimpleNamespace(
            content_type="application/pdf",
            file=BytesIO(b"%PDF-1.4 tiny"),
        )
        validate_upload(ok)

        bad_type = SimpleNamespace(
            content_type="application/exe",
            file=BytesIO(b"x"),
        )
        with pytest.raises(ValueError, match="Unsupported"):
            validate_upload(bad_type)

        big = SimpleNamespace(
            content_type="image/jpeg",
            file=BytesIO(b"0" * (11 * 1024 * 1024)),
        )
        with pytest.raises(ValueError, match="too large"):
            validate_upload(big)


class TestNextKinCrossAccess:
    def test_blank_email_rejected(self):
        err = validate_nextkin_required_fields(
            full_name="Jane",
            email="   ",
            relationship="Friend",
            require_password=False,
        )
        assert err == "Email is required"

    def test_unknown_access_defaults_to_full_kit(self):
        assert normalize_access_level("mystery") == FULL_KIT_ACCESS

    def test_create_request_strips_and_accepts_valid(self):
        req = NextKinCreateRequest(
            email="jane@example.com",
            full_name=" Jane Doe ",
            relationship=" Sister ",
            access_level=FULL_KIT_ACCESS,
            master_password="TempPass123!",
        )
        assert req.email == "jane@example.com"
        assert req.full_name == "Jane Doe"
        assert req.relationship == "Sister"

    def test_create_request_rejects_invalid_email(self):
        with pytest.raises(ValidationError):
            NextKinCreateRequest(
                email="not-an-email",
                full_name="Jane",
                relationship="Friend",
            )

    def test_section_specific_nextkin_can_read_allowed_only(self):
        user = {
            "role": "nextkin",
            "approved": True,
            "immediate_access": True,
            "access_level": SECTION_SPECIFIC_ACCESS,
            "authorized_sections": ["1", "4"],
        }
        assert_section_read_access(user, "1")
        with pytest.raises(HTTPException) as exc:
            assert_section_read_access(user, "7")
        assert exc.value.status_code == 403

    def test_empty_section_payloads(self):
        assert is_effectively_empty({"6A": {"home_address": ""}}) is True
        assert is_effectively_empty({"6A": {"home_address": "x"}}) is False


class TestOtpCountryHelpers:
    def test_detect_phone_country_and_allowlist(self):
        from app.auth.otp_security import (
            detect_phone_country,
            ensure_country_allowed,
            get_allowed_countries,
        )

        assert detect_phone_country("+12025550123") == "US"
        allowed = get_allowed_countries()
        assert isinstance(allowed, set)

        with patch("app.auth.otp_security.get_allowed_countries", return_value={"US"}):
            assert ensure_country_allowed("+12025550123") == "US"
            with pytest.raises(HTTPException) as exc:
                ensure_country_allowed("+2348012345678")
            assert exc.value.status_code == 403
