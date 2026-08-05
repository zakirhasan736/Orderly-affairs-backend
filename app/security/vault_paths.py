"""Shared vault API path rules for billing, audit, and role guards."""

from __future__ import annotations

VAULT_PATH_PREFIXES = (
    "/sections/",
    "/kit",
    "/uploads",
    "/message",
    "/nok-letter",
    "/ai/",
    "/e2ee/vault",
)

VAULT_EXEMPT_PREFIXES = (
    "/billing",
    "/auth",
    "/webhooks",
    "/admin",
    "/support",
    "/feedback",
    "/onboarding",
)

_SKIP_METHODS = frozenset({"OPTIONS", "HEAD"})


def is_vault_api_path(path: str) -> bool:
    if not any(path.startswith(prefix) for prefix in VAULT_PATH_PREFIXES):
        return False
    return not any(path.startswith(prefix) for prefix in VAULT_EXEMPT_PREFIXES)


def extract_section_id_from_path(path: str) -> str | None:
    """Best-effort section id from /sections/section{N}-... or /kit/section/{id}."""
    if path.startswith("/sections/section"):
        rest = path[len("/sections/section") :]
        digits = []
        for ch in rest:
            if ch.isdigit():
                digits.append(ch)
            else:
                break
        return "".join(digits) or None
    if "/kit/section/" in path:
        tail = path.split("/kit/section/", 1)[1]
        section_id = tail.split("/", 1)[0]
        return section_id or None
    return None
