"""
Portal roles for family collaborators on the owner dashboard.

Auth role stays `nextkin` (cookie session). `access_type=family` + `portal_role`
control what they can do. Next-of-Kin (Section 2) ignore portal roles (read-only).
"""

from __future__ import annotations

from typing import Any

# Stable role ids stored on the user document
PORTAL_ROLES: dict[str, dict[str, Any]] = {
    "viewer": {
        "label": "Viewer",
        "description": (
            "View granted dashboard vault sections only. Cannot edit fields, "
            "upload documents, manage billing, family roles, or Next of Kin."
        ),
        "can_read": True,
        "can_write": False,
        "can_upload": False,
        "can_manage_family_access": False,
        "can_manage_nextkin": False,
        "can_manage_billing": False,
        "can_view_vault_settings": False,
    },
    "editor": {
        "label": "Editor",
        "description": (
            "View and update granted vault sections, including drag-and-drop "
            "document uploads. Cannot manage family roles, Next of Kin, billing, "
            "or Vault Settings."
        ),
        "can_read": True,
        "can_write": True,
        "can_upload": True,
        "can_manage_family_access": False,
        "can_manage_nextkin": False,
        "can_manage_billing": False,
        "can_view_vault_settings": False,
    },
    "portal_manager": {
        "label": "Portal Manager",
        "description": (
            "Editor rights on granted areas, plus invite/edit other family "
            "collaborators (role & area access). Cannot approve/delete Next of Kin "
            "or change billing. Vault Settings family area only — MFA stays owner-only."
        ),
        "can_read": True,
        "can_write": True,
        "can_upload": True,
        "can_manage_family_access": True,
        "can_manage_nextkin": False,
        "can_manage_billing": False,
        "can_view_vault_settings": True,
    },
    "admin": {
        "label": "Admin",
        "description": (
            "Full edit/upload on granted dashboard areas, manage family collaborators, "
            "and manage Section 2 Next of Kin (approve, revoke, delete). "
            "Cannot change owner billing or owner MFA."
        ),
        "can_read": True,
        "can_write": True,
        "can_upload": True,
        "can_manage_family_access": True,
        "can_manage_nextkin": True,
        "can_manage_billing": False,
        "can_view_vault_settings": True,
    },
    "super_admin": {
        "label": "Super Admin",
        "description": (
            "Highest family collaborator role: edit/upload granted areas, manage "
            "family access, manage Next of Kin, and view billing status in Vault "
            "Settings (payment changes still require the owner)."
        ),
        "can_read": True,
        "can_write": True,
        "can_upload": True,
        "can_manage_family_access": True,
        "can_manage_nextkin": True,
        "can_manage_billing": True,
        "can_view_vault_settings": True,
    },
}

DEFAULT_PORTAL_ROLE = "viewer"

ROLE_ALIASES = {
    "view": "viewer",
    "read": "viewer",
    "read_only": "viewer",
    "readonly": "viewer",
    "edit": "editor",
    "writer": "editor",
    "manager": "portal_manager",
    "portal-manager": "portal_manager",
    "portalmanager": "portal_manager",
    "family_admin": "admin",
    "kit_admin": "admin",
    "superadmin": "super_admin",
    "super-admin": "super_admin",
}


def normalize_portal_role(raw: str | None) -> str:
    value = str(raw or "").strip().lower().replace(" ", "_")
    if not value:
        return DEFAULT_PORTAL_ROLE
    value = ROLE_ALIASES.get(value, value)
    if value not in PORTAL_ROLES:
        return DEFAULT_PORTAL_ROLE
    return value


def portal_role_meta(role: str | None) -> dict[str, Any]:
    key = normalize_portal_role(role)
    meta = PORTAL_ROLES[key]
    return {"id": key, **meta}


def can_write_sections(user: dict | None) -> bool:
    if not user:
        return False
    if user.get("role") == "owner":
        return True
    if user.get("role") != "nextkin":
        return False
    from app.auth.access_types import is_family_collaborator

    if not is_family_collaborator(user):
        return False
    perms = resolve_dashboard_permissions(user)
    return bool(perms.get("can_write"))


def can_upload_documents(user: dict | None) -> bool:
    if not user:
        return False
    if user.get("role") == "owner":
        return True
    from app.auth.access_types import is_family_collaborator

    if not is_family_collaborator(user):
        return False
    return bool(resolve_dashboard_permissions(user).get("can_upload"))


def resolve_dashboard_permissions(user: dict | None) -> dict[str, bool]:
    """Merge role defaults with optional per-user dashboard_permissions overrides."""
    if not user:
        return _empty_perms()
    if user.get("role") == "owner":
        return {
            "can_read": True,
            "can_write": True,
            "can_upload": True,
            "can_manage_family_access": True,
            "can_manage_nextkin": True,
            "can_manage_billing": True,
            "can_view_vault_settings": True,
        }

    meta = portal_role_meta(user.get("portal_role"))
    base = {
        "can_read": bool(meta.get("can_read")),
        "can_write": bool(meta.get("can_write")),
        "can_upload": bool(meta.get("can_upload")),
        "can_manage_family_access": bool(meta.get("can_manage_family_access")),
        "can_manage_nextkin": bool(meta.get("can_manage_nextkin")),
        "can_manage_billing": bool(meta.get("can_manage_billing")),
        "can_view_vault_settings": bool(meta.get("can_view_vault_settings")),
    }
    overrides = user.get("dashboard_permissions") or {}
    if isinstance(overrides, dict):
        for key in base:
            if key in overrides and overrides[key] is not None:
                base[key] = bool(overrides[key])
    return base


def _empty_perms() -> dict[str, bool]:
    return {
        "can_read": False,
        "can_write": False,
        "can_upload": False,
        "can_manage_family_access": False,
        "can_manage_nextkin": False,
        "can_manage_billing": False,
        "can_view_vault_settings": False,
    }


def role_label(role: str | None) -> str:
    return str(portal_role_meta(role).get("label") or "Viewer")


def list_portal_roles_for_api() -> list[dict[str, Any]]:
    return [
        {
            "id": key,
            "label": meta["label"],
            "description": meta["description"],
            "can_write": meta["can_write"],
            "can_upload": meta["can_upload"],
            "can_manage_family_access": meta["can_manage_family_access"],
            "can_manage_nextkin": meta["can_manage_nextkin"],
            "can_manage_billing": meta["can_manage_billing"],
            "can_view_vault_settings": meta["can_view_vault_settings"],
        }
        for key, meta in PORTAL_ROLES.items()
    ]


# Back-compat alias used by older code
def can_manage_access(user: dict | None) -> bool:
    return bool(resolve_dashboard_permissions(user).get("can_manage_family_access"))
