"""Family portal RBAC matrix — Viewer → Super Admin."""

from __future__ import annotations

from app.auth.portal_roles import (
    PORTAL_ROLES,
    can_upload_documents,
    can_write_sections,
    resolve_dashboard_permissions,
)


def _family(role: str, **extra):
    return {
        "role": "nextkin",
        "access_type": "family",
        "portal_role": role,
        "immediate_access": True,
        **extra,
    }


class TestPortalRoleMatrix:
    def test_role_definitions_match_product_copy(self):
        assert PORTAL_ROLES["viewer"]["can_write"] is False
        assert PORTAL_ROLES["viewer"]["can_upload"] is False
        assert PORTAL_ROLES["viewer"]["can_manage_family_access"] is False
        assert PORTAL_ROLES["viewer"]["can_manage_nextkin"] is False
        assert PORTAL_ROLES["viewer"]["can_manage_billing"] is False
        assert PORTAL_ROLES["viewer"]["can_view_vault_settings"] is False

        assert PORTAL_ROLES["editor"]["can_write"] is True
        assert PORTAL_ROLES["editor"]["can_upload"] is True
        assert PORTAL_ROLES["editor"]["can_manage_family_access"] is False
        assert PORTAL_ROLES["editor"]["can_manage_nextkin"] is False
        assert PORTAL_ROLES["editor"]["can_view_vault_settings"] is False

        assert PORTAL_ROLES["portal_manager"]["can_write"] is True
        assert PORTAL_ROLES["portal_manager"]["can_manage_family_access"] is True
        assert PORTAL_ROLES["portal_manager"]["can_manage_nextkin"] is False
        assert PORTAL_ROLES["portal_manager"]["can_view_vault_settings"] is True
        assert PORTAL_ROLES["portal_manager"]["can_manage_billing"] is False

        assert PORTAL_ROLES["admin"]["can_manage_nextkin"] is True
        assert PORTAL_ROLES["admin"]["can_manage_billing"] is False
        assert PORTAL_ROLES["admin"]["can_view_vault_settings"] is True

        assert PORTAL_ROLES["super_admin"]["can_manage_billing"] is True
        assert PORTAL_ROLES["super_admin"]["can_manage_nextkin"] is True

    def test_viewer_cannot_write_or_upload(self):
        user = _family("viewer")
        assert can_write_sections(user) is False
        assert can_upload_documents(user) is False
        perms = resolve_dashboard_permissions(user)
        assert perms["can_write"] is False
        assert perms["can_upload"] is False
        assert perms["can_manage_family_access"] is False
        assert perms["can_manage_nextkin"] is False

    def test_editor_can_write_and_upload_not_manage(self):
        user = _family("editor")
        assert can_write_sections(user) is True
        assert can_upload_documents(user) is True
        perms = resolve_dashboard_permissions(user)
        assert perms["can_manage_family_access"] is False
        assert perms["can_manage_nextkin"] is False
        assert perms["can_view_vault_settings"] is False
        assert perms["can_manage_billing"] is False

    def test_portal_manager_manages_family_not_nok(self):
        user = _family("portal_manager")
        perms = resolve_dashboard_permissions(user)
        assert perms["can_write"] is True
        assert perms["can_upload"] is True
        assert perms["can_manage_family_access"] is True
        assert perms["can_manage_nextkin"] is False
        assert perms["can_view_vault_settings"] is True
        assert perms["can_manage_billing"] is False

    def test_admin_manages_nok_not_billing(self):
        user = _family("admin")
        perms = resolve_dashboard_permissions(user)
        assert perms["can_manage_family_access"] is True
        assert perms["can_manage_nextkin"] is True
        assert perms["can_manage_billing"] is False

    def test_super_admin_billing_view(self):
        user = _family("super_admin")
        perms = resolve_dashboard_permissions(user)
        assert perms["can_manage_billing"] is True
        assert perms["can_manage_nextkin"] is True
        assert perms["can_manage_family_access"] is True

    def test_override_cannot_elevate_viewer(self):
        user = _family(
            "viewer",
            dashboard_permissions={
                "can_write": True,
                "can_upload": True,
                "can_manage_nextkin": True,
            },
        )
        perms = resolve_dashboard_permissions(user)
        assert perms["can_write"] is False
        assert perms["can_upload"] is False
        assert perms["can_manage_nextkin"] is False

    def test_override_can_reduce_editor(self):
        user = _family(
            "editor",
            dashboard_permissions={"can_upload": False},
        )
        perms = resolve_dashboard_permissions(user)
        assert perms["can_write"] is True
        assert perms["can_upload"] is False

    def test_true_nok_cannot_write(self):
        user = {
            "role": "nextkin",
            "access_type": "nextkin",
            "portal_role": "admin",
            "immediate_access": True,
        }
        assert can_write_sections(user) is False
        assert can_upload_documents(user) is False
