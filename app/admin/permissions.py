"""Admin panel role catalog, access areas, and permission helpers."""

from __future__ import annotations

from typing import Any

# Nav / module keys — must match frontend ADMIN_NAV ids / routes.
ADMIN_AREAS: list[dict[str, str]] = [
    {"id": "overview", "label": "Overview"},
    {"id": "users", "label": "Users"},
    {"id": "activity", "label": "Activity monitor"},
    {"id": "analytics", "label": "Analytics"},
    {"id": "subscriptions", "label": "Subscriptions"},
    {"id": "coupons", "label": "Coupon codes"},
    {"id": "billing", "label": "Billing & payments"},
    {"id": "notifications", "label": "Notifications"},
    {"id": "support", "label": "Support tools"},
    {"id": "feedback", "label": "Feedback"},
    {"id": "dsar", "label": "DSAR tracker"},
    {"id": "legacy", "label": "Legacy access"},
    {"id": "roles", "label": "Roles & permissions"},
    {"id": "audit", "label": "Audit log"},
    {"id": "security", "label": "Security"},
    {"id": "backups", "label": "Backups"},
]

AREA_IDS = [a["id"] for a in ADMIN_AREAS]

# Built-in roles (custom roles can be added in Mongo).
BUILTIN_ROLES: dict[str, dict[str, Any]] = {
    "super_admin": {
        "id": "super_admin",
        "label": "Super Admin",
        "description": "Full platform control including roles, deletes, and lifetime coupons.",
        "areas": ["*"],
        "can_manage_roles": True,
        "can_delete_users": True,
        "can_issue_lifetime_coupons": True,
        "builtin": True,
    },
    "admin": {
        "id": "admin",
        "label": "Admin",
        "description": "Day-to-day operations across users, billing, support, and notifications.",
        "areas": [
            "overview",
            "users",
            "activity",
            "analytics",
            "subscriptions",
            "billing",
            "notifications",
            "support",
            "feedback",
            "dsar",
            "legacy",
            "audit",
            "security",
            "backups",
        ],
        "can_manage_roles": False,
        "can_delete_users": False,
        "can_issue_lifetime_coupons": False,
        "builtin": True,
    },
    "editor": {
        "id": "editor",
        "label": "Editor",
        "description": "Edit user metadata and send notifications; no billing grants or role changes.",
        "areas": [
            "overview",
            "users",
            "activity",
            "notifications",
            "support",
            "feedback",
        ],
        "can_manage_roles": False,
        "can_delete_users": False,
        "can_issue_lifetime_coupons": False,
        "builtin": True,
    },
    "viewer": {
        "id": "viewer",
        "label": "Viewer",
        "description": "Read-only access to allowed areas — never vault contents.",
        "areas": [
            "overview",
            "users",
            "activity",
            "analytics",
            "subscriptions",
            "billing",
            "audit",
        ],
        "can_manage_roles": False,
        "can_delete_users": False,
        "can_issue_lifetime_coupons": False,
        "read_only": True,
        "builtin": True,
    },
    "support": {
        "id": "support",
        "label": "Support",
        "description": "Support inbox, feedback, and limited user lookups.",
        "areas": ["overview", "users", "support", "feedback", "dsar"],
        "can_manage_roles": False,
        "can_delete_users": False,
        "can_issue_lifetime_coupons": False,
        "builtin": True,
    },
}

# Zip-style permission matrix rows: name, note, flags per role column order
PERM_MATRIX_COLUMNS = [
    "super_admin",
    "admin",
    "editor",
    "viewer",
    "support",
]

PERM_MATRIX_ROWS: list[dict[str, Any]] = [
    {
        "name": "View account metadata",
        "note": "Names, plans, status — never vault contents",
        "flags": [1, 1, 1, 1, 1],
    },
    {
        "name": "Edit user profile & email",
        "note": "Login email changes notify both addresses",
        "flags": [1, 1, 1, 0, 0],
    },
    {
        "name": "Suspend / reinstate accounts",
        "note": "Blocks sign-in immediately",
        "flags": [1, 1, 0, 0, 0],
    },
    {
        "name": "Delete accounts",
        "note": "Soft-delete / revoke access",
        "flags": [1, 0, 0, 0, 0],
    },
    {
        "name": "Manage subscriptions & comps",
        "note": "Pause, cancel, grant complimentary access",
        "flags": [1, 1, 0, 0, 0],
    },
    {
        "name": "Issue & revoke coupon codes",
        "note": "Includes lifetime codes (super admin)",
        "flags": [1, 0, 0, 0, 0],
    },
    {
        "name": "Send notifications",
        "note": "Broadcast and single-user messages",
        "flags": [1, 1, 1, 0, 1],
    },
    {
        "name": "Manage admin roles",
        "note": "Invite staff, set areas — always audited",
        "flags": [1, 0, 0, 0, 0],
    },
    {
        "name": "Support tools & feedback",
        "note": "Live inbox and product feedback",
        "flags": [1, 1, 1, 0, 1],
    },
    {
        "name": "Restore encrypted backups",
        "note": "Super Admin only · replaces Mongo from .oa1b package",
        "flags": [1, 0, 0, 0, 0],
    },
]

# Legacy aliases stored on older users
ROLE_ALIASES = {
    "system_owner": "super_admin",
    "owner": "super_admin",
    "support_lead": "admin",
    "read_only": "viewer",
    "support_readonly": "viewer",
}


def normalize_admin_role(role: str | None) -> str:
    raw = (role or "").strip().lower().replace(" ", "_").replace("-", "_")
    if raw in ROLE_ALIASES:
        return ROLE_ALIASES[raw]
    if raw in BUILTIN_ROLES:
        return raw
    return raw or "viewer"


def resolve_areas_for_role(
    role_id: str,
    custom_areas: list[str] | None = None,
    role_def: dict | None = None,
) -> list[str]:
    if custom_areas is not None and len(custom_areas) > 0:
        if "*" in custom_areas:
            return ["*"]
        return [a for a in custom_areas if a in AREA_IDS]

    defn = role_def or BUILTIN_ROLES.get(normalize_admin_role(role_id))
    if not defn:
        return ["overview"]
    areas = defn.get("areas") or ["overview"]
    if "*" in areas:
        return ["*"]
    return [a for a in areas if a in AREA_IDS]


def user_has_area(user: dict, area: str) -> bool:
    role = normalize_admin_role(user.get("admin_role"))
    areas = user.get("admin_areas")
    if not isinstance(areas, list) or not areas:
        areas = resolve_areas_for_role(role)
    if "*" in areas:
        return True
    return area in areas


def user_can_manage_roles(user: dict) -> bool:
    role = normalize_admin_role(user.get("admin_role"))
    if role == "super_admin":
        return True
    defn = BUILTIN_ROLES.get(role) or {}
    return bool(defn.get("can_manage_roles"))


def _matrix_flag(role: str, permission_name: str) -> bool:
    """Look up PERM_MATRIX_ROWS by name for a builtin role column."""
    role = normalize_admin_role(role)
    if role not in PERM_MATRIX_COLUMNS:
        # Unknown/custom roles: deny elevating actions (viewer-safe default).
        return False
    col = PERM_MATRIX_COLUMNS.index(role)
    for row in PERM_MATRIX_ROWS:
        if row.get("name") == permission_name:
            flags = row.get("flags") or []
            return bool(flags[col]) if col < len(flags) else False
    return False


def user_is_read_only(user: dict) -> bool:
    role = normalize_admin_role(user.get("admin_role"))
    defn = BUILTIN_ROLES.get(role) or {}
    return bool(defn.get("read_only"))


def user_can_edit_profile_email(user: dict) -> bool:
    if user_is_read_only(user):
        return False
    return _matrix_flag(user.get("admin_role"), "Edit user profile & email")


def user_can_suspend_accounts(user: dict) -> bool:
    if user_is_read_only(user):
        return False
    return _matrix_flag(user.get("admin_role"), "Suspend / reinstate accounts")


def user_can_delete_users(user: dict) -> bool:
    role = normalize_admin_role(user.get("admin_role"))
    if role == "super_admin":
        return True
    defn = BUILTIN_ROLES.get(role) or {}
    return bool(defn.get("can_delete_users"))


def user_can_clear_rate_limits(user: dict) -> bool:
    """Unstick lockouts — support may clear limits but cannot reinstate."""
    if user_is_read_only(user):
        return False
    role = normalize_admin_role(user.get("admin_role"))
    return role in ("super_admin", "admin", "editor", "support")


def user_can_force_logout(user: dict) -> bool:
    if user_is_read_only(user):
        return False
    role = normalize_admin_role(user.get("admin_role"))
    return role in ("super_admin", "admin", "editor", "support")


def user_can_manage_subscriptions(user: dict) -> bool:
    """Grant/revoke comps, billing mutations — not viewers."""
    if user_is_read_only(user):
        return False
    return _matrix_flag(user.get("admin_role"), "Manage subscriptions & comps")


def user_can_issue_coupons(user: dict) -> bool:
    """Create/revoke platform coupons (matrix: super_admin only)."""
    if user_is_read_only(user):
        return False
    return _matrix_flag(user.get("admin_role"), "Issue & revoke coupon codes")


def user_can_issue_lifetime_coupons(user: dict) -> bool:
    if user_is_read_only(user):
        return False
    role = normalize_admin_role(user.get("admin_role"))
    if role == "super_admin":
        return True
    defn = BUILTIN_ROLES.get(role) or {}
    return bool(defn.get("can_issue_lifetime_coupons"))


def user_can_issue_refunds(user: dict) -> bool:
    """Stripe refunds — same bar as subscription management."""
    return user_can_manage_subscriptions(user)


def user_can_clear_all_rate_limits(user: dict) -> bool:
    """Unscoped wipe of every auth rate-limit doc — super_admin only."""
    return normalize_admin_role(user.get("admin_role")) == "super_admin"
