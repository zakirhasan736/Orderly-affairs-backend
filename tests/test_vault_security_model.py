"""Tests for official vault security model — principals, RBAC, ABAC."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.auth.access_types import ACCESS_TYPE_FAMILY, ACCESS_TYPE_NEXTKIN
from app.auth.nextkin_validation import (
    FULL_KIT_ACCESS,
    SECTION_SPECIFIC_ACCESS,
)
from app.security.access_control import assert_section_read_access
from app.security.vault_paths import is_nok_only_api_path
from app.security.vault_principals import (
    PRINCIPAL_FAMILY,
    PRINCIPAL_NOK,
    PRINCIPAL_OWNER,
    require_nok_principal,
    resolve_principal,
)


class TestPrincipals:
    def test_resolve_owner(self):
        assert resolve_principal({"role": "owner"}) == PRINCIPAL_OWNER

    def test_resolve_nok(self):
        user = {
            "role": "nextkin",
            "access_type": ACCESS_TYPE_NEXTKIN,
        }
        assert resolve_principal(user) == PRINCIPAL_NOK

    def test_resolve_family(self):
        user = {
            "role": "nextkin",
            "access_type": ACCESS_TYPE_FAMILY,
        }
        assert resolve_principal(user) == PRINCIPAL_FAMILY

    def test_require_nok_rejects_family(self):
        family = {
            "role": "nextkin",
            "access_type": ACCESS_TYPE_FAMILY,
            "immediate_access": True,
        }
        with pytest.raises(HTTPException) as exc:
            require_nok_principal(family)
        assert exc.value.status_code == 403


class TestAbacNokHiddenSections:
    def test_nok_denied_section_2(self):
        user = {
            "role": "nextkin",
            "access_type": ACCESS_TYPE_NEXTKIN,
            "immediate_access": True,
            "access_level": FULL_KIT_ACCESS,
        }
        with pytest.raises(HTTPException) as exc:
            assert_section_read_access(user, "2")
        assert exc.value.status_code == 403

    def test_family_allowed_section_2_when_granted(self):
        user = {
            "role": "nextkin",
            "access_type": ACCESS_TYPE_FAMILY,
            "immediate_access": True,
            "access_level": SECTION_SPECIFIC_ACCESS,
            "authorized_sections": ["2"],
        }
        assert_section_read_access(user, "2")


class TestNokOnlyPaths:
    def test_nok_kit_paths(self):
        assert is_nok_only_api_path("/kit/nok", "GET") is True
        assert is_nok_only_api_path("/kit/for-nok", "GET") is True
        assert is_nok_only_api_path("/auth/nextkin/report-owner-deceased", "POST") is True

    def test_family_dashboard_paths_not_nok_only(self):
        assert is_nok_only_api_path("/sections/section5-vehicles", "GET") is False
        assert is_nok_only_api_path("/kit", "GET") is False

    def test_checklist_post_is_nok_only(self):
        assert is_nok_only_api_path("/kit/checklist", "POST") is True
        assert is_nok_only_api_path("/kit/checklist", "GET") is False
