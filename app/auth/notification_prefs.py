"""
Owner vault notification preferences.

Push delivery still requires each device to grant browser permission.
`push_state` is the vault policy; `push_for_collaborators` tells family /
NOK sessions to prompt for device permission when the owner has push Active.
"""

from __future__ import annotations

from typing import Any

PUSH_STATES = frozenset({"active", "paused", "off"})
SPECIAL_DAY_KINDS = frozenset({"birthday", "anniversary", "custom"})

DEFAULT_NOTIFICATION_PREFS: dict[str, Any] = {
    "in_app_enabled": True,
    "email_reminders_enabled": True,
    "push_state": "off",
    "push_for_collaborators": True,
    # None = every eligible immediate-access person. [] = nobody.
    "section_update_recipient_ids": None,
    # Per-section overrides: { "7": ["id1", "id2"] }. Missing key = use default.
    "section_update_recipients_by_section": {},
    "special_days_enabled": True,
    "special_days": [],
}


def _normalize_id_list(raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return None


def _normalize_by_section(raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, value in raw.items():
        section_id = str(key).strip()
        if not section_id:
            continue
        ids = _normalize_id_list(value)
        if ids is None:
            continue
        out[section_id] = ids
    return out


def normalize_special_days(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    days: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            month = int(item.get("month") or 0)
            day = int(item.get("day") or 0)
        except (TypeError, ValueError):
            continue
        if month < 1 or month > 12 or day < 1 or day > 31:
            continue
        kind = str(item.get("kind") or "custom").strip().lower()
        if kind not in SPECIAL_DAY_KINDS:
            kind = "custom"
        stamp = (kind, month, day)
        if stamp in seen:
            continue
        seen.add(stamp)
        label = str(item.get("label") or "").strip()
        if not label:
            label = {
                "birthday": "Birthday",
                "anniversary": "Anniversary",
            }.get(kind, "Special day")
        days.append(
            {
                "kind": kind,
                "month": month,
                "day": day,
                "label": label[:80],
                "enabled": bool(item.get("enabled", True)),
                "source": str(item.get("source") or "owner").strip() or "owner",
            }
        )
    return days[:20]


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
        "section_update_recipient_ids": _normalize_id_list(
            data.get("section_update_recipient_ids", None)
        ),
        "section_update_recipients_by_section": _normalize_by_section(
            data.get("section_update_recipients_by_section")
        ),
        "special_days_enabled": bool(data.get("special_days_enabled", True)),
        "special_days": normalize_special_days(data.get("special_days")),
    }


def get_owner_notification_prefs(owner: dict | None) -> dict[str, Any]:
    if not owner:
        return dict(DEFAULT_NOTIFICATION_PREFS)
    return normalize_notification_prefs(owner.get("notification_prefs"))


def resolve_section_update_recipient_ids(
    prefs: dict[str, Any] | None,
    section_id: str,
) -> list[str] | None:
    """None = default (every eligible person). [] = nobody. Else explicit ids."""
    data = prefs or {}
    by_section = data.get("section_update_recipients_by_section") or {}
    key = str(section_id)
    if key in by_section:
        ids = by_section.get(key)
        return list(ids) if isinstance(ids, list) else []
    return _normalize_id_list(data.get("section_update_recipient_ids", None))


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
    if "section_update_recipient_ids" in patch:
        current["section_update_recipient_ids"] = _normalize_id_list(
            patch.get("section_update_recipient_ids")
        )
    if "section_update_recipients_by_section" in patch:
        incoming = patch.get("section_update_recipients_by_section")
        if incoming is None:
            current["section_update_recipients_by_section"] = {}
        elif isinstance(incoming, dict):
            merged = dict(current.get("section_update_recipients_by_section") or {})
            for key, value in incoming.items():
                section_id = str(key).strip()
                if not section_id:
                    continue
                if value is None:
                    merged.pop(section_id, None)
                elif isinstance(value, list):
                    merged[section_id] = [
                        str(item).strip() for item in value if str(item).strip()
                    ]
            current["section_update_recipients_by_section"] = merged
    if "special_days_enabled" in patch and patch["special_days_enabled"] is not None:
        current["special_days_enabled"] = bool(patch["special_days_enabled"])
    if "special_days" in patch and patch["special_days"] is not None:
        current["special_days"] = normalize_special_days(patch.get("special_days"))
    return current
