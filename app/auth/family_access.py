"""Family collaborator dashboard area access helpers."""

from __future__ import annotations

from typing import Any

FULL_DASHBOARD_ACCESS = "Full Dashboard Access"
AREA_SPECIFIC_ACCESS = "Area-Specific Access"

# Legacy labels still accepted from older clients
_LEGACY_FULL = {"Full Kit Access", "full", "full_kit", "full_dashboard", ""}
_LEGACY_AREA = {
    "Section-Specific Access",
    "section",
    "limited",
    "section-specific",
    "area-specific",
    "Area-Specific Access",
}

# Special dashboard area ids (alongside vault section ids like "5", "7")
DASHBOARD_AREA_BILLING = "billing"
DASHBOARD_AREA_VAULT_SETTINGS = "vault_settings"
DASHBOARD_AREA_SECTION2_NOK = "section2_nextkin"
DASHBOARD_AREA_OVERVIEW = "overview"

SPECIAL_DASHBOARD_AREAS = {
    DASHBOARD_AREA_OVERVIEW: "Owner dashboard overview",
    DASHBOARD_AREA_BILLING: "Billing & subscription",
    DASHBOARD_AREA_VAULT_SETTINGS: "Vault Settings (roles & security)",
    DASHBOARD_AREA_SECTION2_NOK: "Section 2 — Next of Kin management",
}


def normalize_family_access_level(value: str | None) -> str:
    raw = (value or "").strip()
    lower = raw.lower()
    if not raw or raw in _LEGACY_FULL or "full kit" in lower or "full dashboard" in lower:
        return FULL_DASHBOARD_ACCESS
    if raw in _LEGACY_AREA or "section" in lower or "area-specific" in lower or "area specific" in lower:
        return AREA_SPECIFIC_ACCESS
    if "full" in lower:
        return FULL_DASHBOARD_ACCESS
    return AREA_SPECIFIC_ACCESS


def prepare_family_access_fields(payload: Any) -> dict[str, Any]:
    access_level = normalize_family_access_level(
        getattr(payload, "access_level", None)
    )
    areas = [
        str(s).strip()
        for s in (getattr(payload, "authorized_sections", None) or [])
        if str(s).strip()
    ]
    if access_level == FULL_DASHBOARD_ACCESS:
        areas = []
    elif not areas:
        raise ValueError(
            "Select at least one dashboard area (vault sections, billing, "
            "Vault Settings, or Next of Kin management)"
        )

    full_name = str(getattr(payload, "full_name", "") or "").strip()
    email = str(getattr(payload, "email", "") or "").strip().lower()
    relationship = str(getattr(payload, "relationship", "") or "").strip()
    if not full_name:
        raise ValueError("Full name is required")
    if not email:
        raise ValueError("Email is required")
    if not relationship:
        raise ValueError("Relationship is required")

    return {
        "full_name": full_name,
        "email": email,
        "relationship": relationship,
        # Persist UI-friendly labels; ACL still treats Full Kit synonym as full
        "access_level": (
            "Full Kit Access"
            if access_level == FULL_DASHBOARD_ACCESS
            else "Section-Specific Access"
        ),
        "access_level_label": access_level,
        "authorized_sections": areas,
    }


def family_has_dashboard_area(user: dict, area_id: str) -> bool:
    if user.get("role") == "owner":
        return True
    level = user.get("access_level") or ""
    if level in ("Full Kit Access", FULL_DASHBOARD_ACCESS):
        return True
    allowed = [str(x) for x in (user.get("authorized_sections") or [])]
    if str(area_id) in allowed:
        return True
    # Vault section "2" is Access Management / Next of Kin
    if area_id == DASHBOARD_AREA_SECTION2_NOK and "2" in allowed:
        return True
    if area_id == "2" and DASHBOARD_AREA_SECTION2_NOK in allowed:
        return True
    return False
