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

# Next-of-Kin survivor portal APIs — family collaborators must not call these.
NOK_ONLY_EXACT_PATHS = frozenset({
    "/kit/nok",
    "/kit/for-nok",
    "/auth/nextkin/report-owner-deceased",
    "/auth/nextkin-access",
})

NOK_ONLY_PREFIX_PATHS = (
    "/kit/deliver/",
)


def is_vault_api_path(path: str) -> bool:
    if not any(path.startswith(prefix) for prefix in VAULT_PATH_PREFIXES):
        return False
    return not any(path.startswith(prefix) for prefix in VAULT_EXEMPT_PREFIXES)


def is_nok_only_api_path(path: str, method: str) -> bool:
    """True when the route belongs to the NOK portal, not the family dashboard."""
    if path in NOK_ONLY_EXACT_PATHS:
        return True
    if any(path.startswith(prefix) for prefix in NOK_ONLY_PREFIX_PATHS):
        return True
    if path == "/kit/checklist" and method.upper() == "POST":
        return True
    return False


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
