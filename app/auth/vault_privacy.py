"""Owner vault privacy: what is saved on the server vs device-only vs zero-knowledge."""

from __future__ import annotations

from typing import Any, Literal

from app.auth.vault_sensitive_fields import project_field_for_nok

PrivacyMode = Literal["server", "zero_knowledge", "device_only"]

VALID_MODES = frozenset({"server", "zero_knowledge", "device_only"})

_PRIVACY_CACHE: dict[str, dict[str, Any]] = {}


def cache_owner_privacy(owner_id: str, privacy: dict[str, Any]) -> None:
    _PRIVACY_CACHE[str(owner_id)] = privacy


def cached_owner_privacy(owner_id: str) -> dict[str, Any]:
    return _PRIVACY_CACHE.get(str(owner_id)) or {"rules": []}


def _norm_rule(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    section_id = str(raw.get("sectionId") or raw.get("section_id") or "").strip()
    if not section_id:
        return None
    mode = str(raw.get("mode") or "server").strip().lower()
    if mode not in VALID_MODES:
        mode = "server"
    subsection_id = str(raw.get("subsectionId") or raw.get("subsection_id") or "").strip() or None
    field_key = str(raw.get("fieldKey") or raw.get("field_key") or "").strip() or None
    share = bool(raw.get("shareWithNok", raw.get("share_with_nok", True)))
    if mode != "server":
        share = False
    return {
        "sectionId": section_id,
        "subsectionId": subsection_id,
        "fieldKey": field_key,
        "mode": mode,
        "shareWithNok": share,
    }


def normalize_vault_privacy(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    rules_in = data.get("rules") if isinstance(data.get("rules"), list) else []
    rules = []
    seen: set[str] = set()
    for item in rules_in:
        rule = _norm_rule(item)
        if not rule:
            continue
        key = f"{rule['sectionId']}|{rule['subsectionId'] or ''}|{rule['fieldKey'] or ''}"
        if key in seen:
            continue
        seen.add(key)
        rules.append(rule)
        if len(rules) >= 800:
            break
    return {"rules": rules}


def get_owner_vault_privacy(owner: dict | None) -> dict[str, Any]:
    if not owner:
        return {"rules": []}
    return normalize_vault_privacy(owner.get("vault_privacy"))


def rule_specificity(rule: dict[str, Any]) -> int:
    score = 1
    if rule.get("subsectionId"):
        score += 2
    if rule.get("fieldKey"):
        score += 4
    return score


def resolve_mode(
    privacy: dict[str, Any],
    *,
    section_id: str,
    subsection_id: str | None = None,
    field_key: str | None = None,
) -> str:
    rules = privacy.get("rules") if isinstance(privacy, dict) else []
    if not isinstance(rules, list):
        return "server"
    best = None
    best_score = -1
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if str(rule.get("sectionId") or "") != str(section_id):
            continue
        rule_sub = rule.get("subsectionId") or None
        rule_field = rule.get("fieldKey") or None
        if rule_sub and str(rule_sub) != str(subsection_id or ""):
            continue
        if rule_field and str(rule_field) != str(field_key or ""):
            continue
        if rule_field and not field_key:
            continue
        if not rule_sub and subsection_id and rule_field:
            continue
        score = rule_specificity(rule)
        if score > best_score:
            best = rule
            best_score = score
    return str((best or {}).get("mode") or "server")


def strip_hidden_fields_for_nok(
    data: Any,
    privacy: dict[str, Any],
    section_id: str,
) -> Any:
    """Drop device-only and zero-knowledge values from a NOK/kit payload."""
    if not isinstance(data, dict):
        return data

    def keep_field(subsection_id: str, field_key: str) -> bool:
        return (
            resolve_mode(
                privacy,
                section_id=section_id,
                subsection_id=subsection_id,
                field_key=field_key,
            )
            == "server"
        )

    out: dict[str, Any] = {}
    for key, value in data.items():
        if str(key).startswith("_oa_"):
            continue
        if (
            resolve_mode(privacy, section_id=section_id, subsection_id=str(key))
            != "server"
        ):
            continue
        if isinstance(value, list):
            next_items = []
            for item in value:
                if not isinstance(item, dict):
                    next_items.append(item)
                    continue
                projected: dict[str, Any] = {}
                for field, field_val in item.items():
                    if not keep_field(str(key), str(field)):
                        continue
                    next_val = project_field_for_nok(section_id, str(field), field_val)
                    if next_val is None:
                        continue
                    projected[field] = next_val
                next_items.append(projected)
            out[key] = next_items
        elif isinstance(value, dict):
            projected = {}
            for field, field_val in value.items():
                if not keep_field(str(key), str(field)):
                    continue
                next_val = project_field_for_nok(section_id, str(field), field_val)
                if next_val is None:
                    continue
                projected[field] = next_val
            out[key] = projected
        else:
            next_val = project_field_for_nok(section_id, str(key), value)
            if next_val is not None:
                out[key] = next_val
    return out
