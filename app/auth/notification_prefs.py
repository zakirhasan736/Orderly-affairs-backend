"""
Owner vault notification preferences.

Push delivery still requires each device to grant browser permission.
`push_state` is the vault policy; `push_for_collaborators` tells family /
NOK sessions to prompt for device permission when the owner has push Active.
"""

from __future__ import annotations

from typing import Any

PUSH_STATES = frozenset({"active", "paused", "off"})

DEFAULT_NOTIFICATION_PREFS: dict[str, Any] = {
    "in_app_enabled": True,
    "email_reminders_enabled": True,
    "push_state": "off",
    "push_for_collaborators": True,
}


def normalize_notification_prefs(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    push_state = str(data.get("push_state") or "off").lower()
    if push_state not in PUSH_STATES:
        push_state = "off"
    return {
        "in_app_enabled": bool(
            data.get("in_app_enabled", DEFAULT_NOTIFICATION_PREFS["in_app_enabled"])
        ),
        "email_reminders_enabled": bool(
            data.get(
                "email_reminders_enabled",
                DEFAULT_NOTIFICATION_PREFS["email_reminders_enabled"],
            )
        ),
        "push_state": push_state,
        "push_for_collaborators": bool(
            data.get(
                "push_for_collaborators",
                DEFAULT_NOTIFICATION_PREFS["push_for_collaborators"],
            )
        ),
    }


def get_owner_notification_prefs(owner: dict | None) -> dict[str, Any]:
    if not owner:
        return dict(DEFAULT_NOTIFICATION_PREFS)
    return normalize_notification_prefs(owner.get("notification_prefs"))


def vault_push_session_payload(owner: dict | None) -> dict[str, Any]:
    """Exposed on /session and /nextkin-access for every vault participant."""
    prefs = get_owner_notification_prefs(owner)
    collaborators = bool(
        prefs["push_for_collaborators"] and prefs["push_state"] == "active"
    )
    return {
        "state": prefs["push_state"],
        "collaborators_enabled": collaborators,
    }


def merge_notification_prefs_patch(
    existing: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    current = normalize_notification_prefs(existing)
    if "in_app_enabled" in patch and patch["in_app_enabled"] is not None:
        current["in_app_enabled"] = bool(patch["in_app_enabled"])
    if (
        "email_reminders_enabled" in patch
        and patch["email_reminders_enabled"] is not None
    ):
        current["email_reminders_enabled"] = bool(patch["email_reminders_enabled"])
    if "push_state" in patch and patch["push_state"] is not None:
        state = str(patch["push_state"]).lower()
        if state not in PUSH_STATES:
            raise ValueError("push_state must be active, paused, or off")
        current["push_state"] = state
    if (
        "push_for_collaborators" in patch
        and patch["push_for_collaborators"] is not None
    ):
        current["push_for_collaborators"] = bool(patch["push_for_collaborators"])
    return current
