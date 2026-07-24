def _humanize_key(key: str) -> str:
    if not key:
        return "Field"

    return key.replace("_", " ").strip().title()


def format_field_catalog_prompt(catalog: list[dict] | None) -> str:
    if not catalog:
        return (
            "\n\nForm filling rules:\n"
            "- Use the exact field keys required by the response schema patch.\n"
            "- Put extracted values into the closest matching form fields.\n"
            "- For note/location/document fields, return a clear plain string.\n"
            "- Skip fields with no supporting evidence in the document.\n"
            "- Fill EVERY field that the document supports — do not leave readable data unused.\n"
        )

    lines: list[str] = []

    for item in catalog:
        if not isinstance(item, dict):
            continue

        key = item.get("key")
        if not key:
            continue

        field_type = item.get("type") or "TextInput"
        if field_type in {"Instructions", "InstructionsModal"}:
            continue

        label = (item.get("label") or "").strip() or _humanize_key(str(key))
        helper = (item.get("helperText") or item.get("helper_text") or "").strip()
        placeholder = (item.get("placeholder") or "").strip()
        options = item.get("options") if isinstance(item.get("options"), list) else []

        line = f'- {key} ("{label}") type={field_type}'
        if helper:
            line += f' — {helper}'
        if placeholder:
            line += f' — placeholder: "{placeholder}"'
        if options:
            option_text = ", ".join(str(opt) for opt in options if opt is not None)
            if option_text:
                line += f' — allowed options: [{option_text}]'

        lines.append(line)

    if not lines:
        return ""

    return (
        "\n\nForm field catalog for this upload area.\n"
        "Placement rules (critical):\n"
        "1) First understand what each extracted value MEANS (even if the document wording differs).\n"
        "2) Compare that meaning to each field's key, label, helper text, and allowed options.\n"
        "3) Place the value into the ONE exact matching field key — do not confuse similar labels.\n"
        "4) Use these exact keys in patch. Never invent alternate key names when a catalog key fits.\n"
        "5) If two fields look similar, prefer the stronger label/helper match; leave the other null.\n"
        "6) Fill ALL fields the document supports. Aim to place every readable fact into a responsible field.\n"
        "7) For Dropdown / RadioButtons / Select fields, return ONE of the allowed option strings exactly (best meaning match).\n"
        "8) For Checkbox fields, return true/false (or yes/no). For single-option RadioButtons (checkbox UI), return that option string when true.\n"
        "9) Field key names in the document may differ — always choose the catalog field whose LABEL or OPTIONS best match the fact.\n"
        + "\n".join(lines)
        + "\n- For TextInputWithUpload fields, return a plain string (notes, location, or extracted text).\n"
        + "- For Dropdown / RadioButtons fields, use one of the allowed option values when possible.\n"
        + "- Skip fields with no supporting evidence in the document.\n"
    )


def build_default_field_catalog_from_schema(schema: dict | None) -> list[dict]:
    """Build a lightweight catalog from JSON schema patch properties."""
    if not isinstance(schema, dict):
        return []

    patch_schema = (
        (schema.get("properties") or {}).get("patch")
        if isinstance(schema.get("properties"), dict)
        else None
    )
    if not isinstance(patch_schema, dict):
        return []

    catalog: list[dict] = []

    def walk(node: dict, prefix: str = ""):
        props = node.get("properties")
        if not isinstance(props, dict):
            return

        for key, child in props.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if not isinstance(child, dict):
                continue

            child_type = child.get("type")
            if child_type == "object" or (
                isinstance(child_type, list) and "object" in child_type
            ):
                if isinstance(child.get("properties"), dict):
                    walk(child, path)
                continue

            if child_type == "array":
                items = child.get("items")
                if isinstance(items, dict) and isinstance(items.get("properties"), dict):
                    walk(items, path)
                continue

            leaf = path.split(".")[-1]
            catalog.append(
                {
                    "key": leaf,
                    "label": _humanize_key(leaf),
                    "type": "TextInput",
                    "helperText": "",
                    "placeholder": "",
                    "options": [],
                }
            )

    walk(patch_schema)
    return catalog


def _flatten_patch_values(patch: object, prefix: str = "") -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []

    if isinstance(patch, dict):
        for key, value in patch.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            results.extend(_flatten_patch_values(value, path))
        return results

    if isinstance(patch, list):
        for index, item in enumerate(patch):
            path = f"{prefix}[{index}]"
            results.extend(_flatten_patch_values(item, path))
        return results

    if patch is None:
        return results

    if isinstance(patch, str) and not patch.strip():
        return results

    results.append((prefix, str(patch)))
    return results


def _label_for_field_path(field_path: str, catalog: list[dict] | None) -> str:
    leaf_key = field_path.split(".")[-1].split("[")[0]
    if catalog:
        for item in catalog:
            if isinstance(item, dict) and item.get("key") == leaf_key:
                label = (item.get("label") or "").strip()
                if label:
                    return label
                helper = (item.get("helperText") or item.get("helper_text") or "").strip()
                if helper:
                    return helper
                placeholder = (item.get("placeholder") or "").strip()
                if placeholder:
                    return placeholder
    return _humanize_key(leaf_key)


def build_extracted_fields_preview(
    patch: object,
    field_catalog: list[dict] | None,
    *,
    limit: int = 12,
) -> list[dict[str, str]]:
    previews: list[dict[str, str]] = []

    for field_path, value in _flatten_patch_values(patch):
        if not value.strip():
            continue

        previews.append(
            {
                "field_path": field_path,
                "field_label": _label_for_field_path(field_path, field_catalog),
                "value": value[:160],
            }
        )

        if len(previews) >= limit:
            break

    return previews


SECTION_PREVIEW_FIELD_KEYS: dict[str, list[str]] = {
    "vital_information": [
        "full_legal_name",
        "date_of_birth",
        "phone_number",
        "primary_email_username",
        "current_address",
    ],
    "vehicles": [
        "year",
        "make",
        "model",
        "vin",
        "license_plate",
        "registration_expiry",
        "insurance_company",
        "insurance_policy",
    ],
    "main_residence": [
        "property_address",
        "mortgage_company",
        "insurance_company",
        "property_tax",
    ],
    "insurance_policies": [
        "policy_type",
        "policy_number",
        "policy_company",
        "policy_expiry",
        "insurance_company",
        "provider",
        "coverage_amount",
    ],
    "banking_financial_accounts": [
        "bank_name",
        "account_type",
        "account_number",
        "routing_number",
    ],
    "investment_accounts": [
        "account_name",
        "institution",
        "account_type",
        "account_number",
    ],
    "health_information": [
        "primary_health_insurance",
        "provider_name",
        "doctor_name",
        "medications",
    ],
    "credit_cards_debt": [
        "card_name",
        "creditor",
        "account_number",
        "balance",
    ],
    "employment_business": [
        "employer",
        "job_title",
        "business_name",
        "income",
    ],
    "legal_documents_records": [
        "document_type",
        "document_name",
        "location",
    ],
}


def build_section_result_preview(
    section_key: str,
    result: dict | None,
    field_catalog: list[dict] | None = None,
    *,
    limit: int = 8,
) -> list[dict[str, str]]:
    from app.ai.document_classifier import get_section_meta

    if not isinstance(result, dict):
        return []

    patch = result.get("patch")
    if not isinstance(patch, dict):
        return []

    meta = get_section_meta(section_key) or {}
    subsection_hint = meta.get("default_subsection")
    priority_keys = SECTION_PREVIEW_FIELD_KEYS.get(section_key, [])
    previews: list[dict[str, str]] = []

    patch_candidates: list[tuple[str, object]] = []

    if subsection_hint and subsection_hint in patch:
        patch_candidates.append((subsection_hint, patch[subsection_hint]))
    else:
        for key, value in patch.items():
            if key in {"section"} or str(key).startswith("_"):
                continue
            if isinstance(value, (list, dict)):
                patch_candidates.append((str(key), value))

    for subsection_key, raw_items in patch_candidates:
        items = raw_items if isinstance(raw_items, list) else [raw_items]

        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue

            keys_to_show = priority_keys or [
                key
                for key, value in item.items()
                if value not in (None, "")
                and not str(key).startswith("_")
                and not str(key).endswith("_instructions")
                and not str(key).endswith("_header")
            ]

            for key in keys_to_show:
                value = item.get(key)
                if value in (None, ""):
                    continue

                if isinstance(value, dict):
                    nested_text = value.get("text") if "text" in value else None
                    text_value = str(nested_text or "").strip()
                else:
                    text_value = str(value).strip()

                if not text_value:
                    continue

                previews.append(
                    {
                        "field_path": f"{subsection_key}[{index}].{key}",
                        "field_label": _label_for_field_path(str(key), field_catalog),
                        "value": text_value[:160],
                    }
                )

                if len(previews) >= limit:
                    return previews

        if previews:
            return previews

    return build_extracted_fields_preview(patch, field_catalog, limit=limit)


def build_section_previews_payload(
    *,
    filled_section_key: str,
    filled_result: dict,
    additional_sections: list[dict],
    cached_extractions: dict,
    field_catalog: list[dict] | None = None,
) -> list[dict]:
    from app.ai.document_classifier import get_section_meta

    previews: list[dict] = []

    filled_meta = get_section_meta(filled_section_key) or {}
    filled_fields = build_section_result_preview(
        filled_section_key,
        filled_result,
        field_catalog,
    )

    if filled_fields:
        previews.append(
            {
                "section_key": filled_section_key,
                "section_id": filled_meta.get("id"),
                "section_label": filled_meta.get("label") or filled_section_key,
                "status": "filled",
                "extracted_fields": filled_fields,
            }
        )

    for item in additional_sections:
        section_key = item.get("section_key")
        if not section_key:
            continue

        cached = cached_extractions.get(section_key)
        extracted_fields = (
            build_section_result_preview(section_key, cached, None)
            if isinstance(cached, dict)
            else []
        )

        previews.append(
            {
                "section_key": section_key,
                "section_id": item.get("section_id"),
                "section_label": item.get("section_label") or section_key,
                "status": "pending",
                "data_summary": item.get("data_summary") or "",
                "extracted_fields": extracted_fields,
            }
        )

        item["extracted_fields"] = extracted_fields

    return previews


def build_fast_section_previews_from_classification(
    classification: dict,
    *,
    suggested_section_key: str,
) -> list[dict]:
    """Lightweight previews from classification only — no extra Gemini calls."""
    from app.ai.document_classifier import (
        build_additional_sections_payload,
        get_section_meta,
    )

    previews: list[dict] = []
    suggested_meta = get_section_meta(suggested_section_key) or {}

    previews.append(
        {
            "section_key": suggested_section_key,
            "section_id": suggested_meta.get("id"),
            "section_label": suggested_meta.get("label") or suggested_section_key,
            "status": "pending",
            "data_summary": classification.get("document_summary") or "",
            "extracted_fields": [],
        }
    )

    for item in build_additional_sections_payload(
        classification,
        suggested_section_key,
    ):
        if item.get("section_key") == suggested_section_key:
            continue
        previews.append(
            {
                "section_key": item.get("section_key"),
                "section_id": item.get("section_id"),
                "section_label": item.get("section_label") or item.get("section_key"),
                "status": "pending",
                "data_summary": item.get("data_summary") or "",
                "extracted_fields": [],
            }
        )

    return previews
