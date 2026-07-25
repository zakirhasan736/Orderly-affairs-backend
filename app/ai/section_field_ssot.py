"""Single source of truth helpers for AI section field keys.

Used to build rich catalogs when the client omits field_catalog, and by
scripts/check_ai_field_drift.py to catch schema ↔ form drift.
"""

from __future__ import annotations

from typing import Any

# Critical fields that must exist in AI schemas AND frontend formConfig / UI.
# Keep labels short — catalogs merge client labels when available.
CANONICAL_SECTION_FIELDS: dict[str, list[dict[str, str]]] = {
    "vehicles": [
        {"key": "year", "label": "Year"},
        {"key": "make", "label": "Make"},
        {"key": "model", "label": "Model"},
        {"key": "vin", "label": "VIN"},
        {"key": "license_plate", "label": "License plate"},
        {"key": "registration_expiry", "label": "Registration expiry"},
        {"key": "insurance_company", "label": "Insurance company"},
        {"key": "insurance_policy", "label": "Insurance policy number"},
        {"key": "notes", "label": "Notes"},
    ],
    "insurance_policies": [
        {"key": "policy_type", "label": "Policy type"},
        {"key": "policy_company", "label": "Insurance company"},
        {"key": "policy_number", "label": "Policy number"},
        {"key": "policy_expiry", "label": "Policy expiry date"},
        {"key": "coverage_amount", "label": "Coverage amount"},
        {"key": "beneficiaries", "label": "Beneficiaries"},
        {"key": "premium_info", "label": "Premium information"},
        {"key": "notes", "label": "Notes"},
    ],
    "community_memberships": [
        {"key": "organization_name", "label": "Organization name"},
        {"key": "organization_type", "label": "Organization type"},
        {"key": "membership_details", "label": "Membership details"},
        {"key": "renewal_date", "label": "Membership renewal date"},
        {"key": "contact_info", "label": "Contact information"},
        {"key": "documents", "label": "Related documents"},
    ],
    "banking_financial_accounts": [
        {"key": "bank_name", "label": "Bank name"},
        {"key": "account_type", "label": "Account type"},
        {"key": "account_number", "label": "Account number"},
        {"key": "routing_number", "label": "Routing number"},
        {"key": "cd_maturity_date", "label": "CD / account maturity date"},
        {"key": "last_statement_date", "label": "Last statement date"},
        {"key": "subscription_renewal_date", "label": "Subscription renewal date"},
        {"key": "service_name", "label": "Digital payment service"},
    ],
    "passwords_online_accounts": [
        {"key": "account_type", "label": "Account type"},
        {"key": "service_name", "label": "Service / website name"},
        {"key": "account_username", "label": "Username"},
        {"key": "subscription_renewal_date", "label": "Subscription renewal date"},
        {"key": "account_expiry_date", "label": "Account / access expiry date"},
        {"key": "account_value", "label": "Account value / importance"},
        {"key": "closure_instructions", "label": "Closure instructions"},
    ],
}

# AI section key → JSON schema module path attribute name
SECTION_SCHEMA_IMPORTS: dict[str, tuple[str, str]] = {
    "vehicles": ("app.ai.schemas.section5_schema", "SECTION5_FULL_SCHEMA"),
    "insurance_policies": ("app.ai.schemas.section7_schema", "SECTION7_FULL_SCHEMA"),
    "community_memberships": ("app.ai.schemas.section8_schema", "SECTION8_FULL_SCHEMA"),
    "banking_financial_accounts": (
        "app.ai.schemas.section12_schema",
        "SECTION12_FULL_SCHEMA",
    ),
    "passwords_online_accounts": (
        "app.ai.schemas.section13_schema",
        "SECTION13_FULL_SCHEMA",
    ),
    "main_residence": ("app.ai.schemas.section6_schema", "SECTION6_FULL_SCHEMA"),
}


def load_section_schema(section_key: str) -> dict | None:
    meta = SECTION_SCHEMA_IMPORTS.get(section_key)
    if not meta:
        return None
    module_path, attr = meta
    try:
        module = __import__(module_path, fromlist=[attr])
        schema = getattr(module, attr, None)
        return schema if isinstance(schema, dict) else None
    except Exception:
        return None


def build_rich_catalog_for_section(section_key: str) -> list[dict[str, Any]]:
    """Prefer canonical labeled fields; fall back to schema-derived keys."""
    from app.ai.field_catalog import build_default_field_catalog_from_schema

    canonical = CANONICAL_SECTION_FIELDS.get(section_key) or []
    schema = load_section_schema(section_key)
    schema_catalog = build_default_field_catalog_from_schema(schema)

    by_key: dict[str, dict[str, Any]] = {}
    for item in schema_catalog:
        key = str(item.get("key") or "")
        if key:
            by_key[key] = dict(item)

    for item in canonical:
        key = item["key"]
        existing = by_key.get(key) or {
            "key": key,
            "type": "TextInput",
            "helperText": "",
            "placeholder": "",
            "options": [],
        }
        existing["label"] = item.get("label") or existing.get("label") or key
        by_key[key] = existing

    return list(by_key.values())


def schema_leaf_keys(schema: dict | None) -> set[str]:
    from app.ai.field_catalog import build_default_field_catalog_from_schema

    return {
        str(item.get("key"))
        for item in build_default_field_catalog_from_schema(schema)
        if item.get("key")
    }
