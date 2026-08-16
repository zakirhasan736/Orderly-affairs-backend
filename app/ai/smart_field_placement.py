"""
Universal smart field placement: understand extracted meaning, then map onto
exact form field keys/labels for ANY section — not only vehicles/insurance.
"""

from __future__ import annotations

import re
from typing import Any


def _tokens(text: str) -> list[str]:
    return [t for t in re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split() if t]


def _norm_key(text: str) -> str:
    return "_".join(_tokens(text))


MEANING_GROUPS: list[dict[str, Any]] = [
    {
        "id": "person_name",
        "terms": [
            "full_legal_name",
            "full_name",
            "legal_name",
            "name",
            "account_holder",
            "cardholder",
            "insured_name",
            "member_name",
            "patient_name",
            "named_insured",
            "policy_holder",
            "policyholder",
        ],
    },
    {
        "id": "date_of_birth",
        "terms": ["date_of_birth", "dob", "birth_date", "birthday"],
    },
    {
        "id": "phone",
        "terms": ["phone", "phone_number", "mobile", "cell", "telephone"],
    },
    {
        "id": "email",
        "terms": ["email", "e_mail", "primary_email", "email_address"],
    },
    {
        "id": "address",
        "terms": [
            "address",
            "current_address",
            "home_address",
            "property_address",
            "mailing_address",
        ],
    },
    {
        "id": "policy_number",
        "terms": [
            "policy_number",
            "policy_no",
            "policy_id",
            "insurance_policy",
            "insurance_number",
            "insurance_no",
            "member_id",
            "certificate_number",
            "plan_number",
            "contract_number",
            "polcy_numbor",
            "policy_numbr",
        ],
    },
    {
        "id": "policy_company",
        "terms": [
            "policy_company",
            "insurance_company",
            "insurance_name",
            "insurance_carrier",
            "carrier",
            "insurer",
            "provider",
            "carrier_name",
        ],
    },
    {
        "id": "expiry_date",
        "terms": [
            "expiry",
            "expiration",
            "expires",
            "policy_expiry",
            "registration_expiry",
            "valid_through",
            "valid_until",
            "end_date",
            "coverage_ends",
            "account_expiry_date",
            "account_expiry",
        ],
    },
    {
        "id": "renewal_date",
        "terms": [
            "renewal_date",
            "renewal",
            "renews",
            "dues_renewal",
            "membership_renewal",
            "subscription_renewal_date",
            "subscription_renewal",
            "plan_renewal",
            "next_billing_date",
        ],
    },
    {
        "id": "maturity_date",
        "terms": [
            "cd_maturity_date",
            "maturity_date",
            "maturity",
            "matures",
            "cd_maturity",
        ],
    },
    {
        "id": "last_statement_date",
        "terms": [
            "last_statement_date",
            "statement_date",
            "statement_as_of",
            "as_of_date",
        ],
    },
    {
        "id": "account_number",
        "terms": ["account_number", "acct_number", "acct_no", "iban"],
    },
    {
        "id": "routing_number",
        "terms": ["routing_number", "routing", "aba", "sort_code"],
    },
    {
        "id": "bank_name",
        "terms": ["bank_name", "bank", "financial_institution", "institution"],
    },
    {
        "id": "employer",
        "terms": ["employer", "employer_name", "company_name", "business_name"],
    },
    {
        "id": "job_title",
        "terms": ["job_title", "title", "position", "occupation"],
    },
    {
        "id": "salary",
        "terms": ["salary", "income", "wages", "compensation"],
    },
    {
        "id": "vin",
        "terms": ["vin", "vehicle_identification_number"],
    },
    {
        "id": "license_plate",
        "terms": ["license_plate", "licence_plate", "plate_number", "tag_number"],
    },
    {
        "id": "coverage_amount",
        "terms": [
            "coverage_amount",
            "coverage_limit",
            "death_benefit",
            "liability_limit",
        ],
    },
    {
        "id": "policy_contact",
        "terms": ["policy_contact", "agent", "agent_name", "broker", "producer"],
    },
    {
        "id": "beneficiaries",
        "terms": ["beneficiary", "beneficiaries"],
    },
    {
        "id": "effective_date",
        "terms": ["effective_date", "effective", "issue_date", "inception_date"],
    },
    {
        "id": "doctor",
        "terms": ["doctor", "doctor_name", "physician", "provider_name"],
    },
    {
        "id": "medication",
        "terms": ["medication", "medications", "prescription"],
    },
    {
        "id": "creditor",
        "terms": ["creditor", "lender", "mortgage_company"],
    },
    {
        "id": "balance",
        "terms": ["balance", "amount_owed", "outstanding", "payoff"],
    },
    {
        "id": "website",
        "terms": ["website", "url", "portal", "platform"],
    },
    {
        "id": "username",
        "terms": ["username", "user_name", "login"],
    },
    {
        "id": "notes",
        "terms": ["notes", "note", "comments", "remarks"],
    },
    {
        "id": "school",
        "terms": ["school", "university", "college"],
    },
    {
        "id": "degree",
        "terms": ["degree", "diploma", "certification"],
    },
    {
        "id": "military_branch",
        "terms": ["branch", "service_branch", "military_branch"],
    },
    {
        "id": "charity",
        "terms": ["charity", "charity_name", "nonprofit"],
    },
    {
        "id": "attorney",
        "terms": ["attorney", "lawyer", "counsel"],
    },
]


def _meaning_ids(text: str) -> set[str]:
    n = _norm_key(text)
    ids: set[str] = set()
    for group in MEANING_GROUPS:
        for term in group["terms"]:
            t = _norm_key(term)
            if not t:
                continue
            if n == t:
                ids.add(group["id"])
            elif len(t) >= 6 and (t in n or n in t):
                ids.add(group["id"])
    return ids


def _jaccard(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def score_field_match(incoming_key: str, field: dict) -> float:
    in_key = _norm_key(incoming_key)
    field_key = _norm_key(str(field.get("key") or ""))
    field_label = _norm_key(str(field.get("label") or ""))
    if not in_key or not field_key:
        return 0.0

    if in_key == field_key:
        return 100.0
    if field_label and in_key == field_label:
        return 96.0

    in_tokens = _tokens(incoming_key)
    key_label_tokens = _tokens(str(field.get("key") or "")) + _tokens(
        str(field.get("label") or "")
    )
    helper_tokens = _tokens(
        str(field.get("helperText") or field.get("helper_text") or "")
    )

    score = 0.0
    score += _jaccard(in_tokens, key_label_tokens) * 40.0
    score += _jaccard(in_tokens, helper_tokens) * 8.0

    shared = _meaning_ids(incoming_key) & _meaning_ids(
        " ".join(
            [
                str(field.get("key") or ""),
                str(field.get("label") or ""),
                str(field.get("helperText") or field.get("helper_text") or ""),
            ]
        )
    )
    if shared:
        score += 45.0
        # Exact synonym of a shared concept (acct_no → account_number)
        for group in MEANING_GROUPS:
            if group["id"] not in shared:
                continue
            for term in group["terms"]:
                if _norm_key(term) == in_key:
                    score = max(score, 88.0)
                    break

    try:
        from app.ai.semantic_field_map import resolve_concept_from_key

        in_concept = resolve_concept_from_key(incoming_key)
        field_concept = resolve_concept_from_key(str(field.get("key") or ""))
        if in_concept and field_concept and in_concept == field_concept:
            score = max(score, 90.0)
    except Exception:
        pass

    if len(in_key) >= 4 and len(field_key) >= 4:
        if in_key in field_key or field_key in in_key:
            score += 12.0
        if field_label and (in_key in field_label or field_label in in_key):
            score += 10.0

    if len(in_tokens) == 1 and len(in_tokens[0]) <= 3:
        score -= 15.0

    return max(0.0, min(100.0, score))


MIN_ACCEPT_SCORE = 28.0


def find_best_field_match(
    incoming_key: str,
    fields: list[dict],
    *,
    used_keys: set[str] | None = None,
    min_score: float = MIN_ACCEPT_SCORE,
) -> tuple[str, float] | None:
    best: tuple[str, float] | None = None
    used = used_keys or set()

    for field in fields:
        if not isinstance(field, dict):
            continue
        key = field.get("key")
        if not key or key in used:
            continue
        field_type = field.get("type") or ""
        if field_type in {"Instructions", "InstructionsModal"}:
            continue

        score = score_field_match(incoming_key, field)
        # Boost when incoming key overlaps option vocabulary
        options = field.get("options") if isinstance(field.get("options"), list) else []
        if options:
            opt_tokens = []
            for opt in options:
                opt_tokens.extend(_tokens(str(opt)))
            score += _jaccard(_tokens(incoming_key), opt_tokens) * 18.0

        if score < min_score:
            continue
        if best is None or score > best[1]:
            best = (str(key), score)

    return best


def _is_empty(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, list) and not value:
        return True
    if isinstance(value, dict) and ("text" in value or "files" in value):
        text = value.get("text")
        files = value.get("files")
        has_text = isinstance(text, str) and bool(text.strip())
        has_files = isinstance(files, list) and bool(files)
        return not (has_text or has_files)
    return False


def smart_place_onto_fields(
    incoming: dict,
    fields: list[dict] | None,
) -> dict:
    """Remap AI keys onto exact catalog field keys using meaning vs labels/options."""
    if not isinstance(incoming, dict):
        return incoming
    if not fields:
        return dict(incoming)

    candidates: list[tuple[str, str, float, Any]] = []

    for key, value in incoming.items():
        if key == "__rowId" or str(key).endswith("_instructions") or str(key).endswith(
            "_header"
        ):
            continue
        if _is_empty(value):
            continue
        if isinstance(value, dict) and not (
            "text" in value or "files" in value
        ):
            # Nested objects handled by smart_place_patch
            continue

        match = find_best_field_match(str(key), fields)
        if match:
            candidates.append((str(key), match[0], match[1], value))

    candidates.sort(key=lambda item: item[2], reverse=True)

    next_obj: dict = {}
    used_targets: set[str] = set()
    used_sources: set[str] = set()

    for from_key, to_key, _score, value in candidates:
        if to_key in used_targets or from_key in used_sources:
            continue
        used_targets.add(to_key)
        used_sources.add(from_key)
        next_obj[to_key] = value

    # Second pass: place by VALUE ↔ dropdown/radio options when key names differ.
    for key, value in incoming.items():
        if key in used_sources:
            continue
        if _is_empty(value):
            continue
        if isinstance(value, (dict, list)):
            continue

        text = str(value).strip()
        if not text or len(text) > 120:
            continue

        best: tuple[str, float] | None = None
        for field in fields:
            if not isinstance(field, dict):
                continue
            field_key = field.get("key")
            if not field_key or field_key in used_targets:
                continue
            field_type = field.get("type") or ""
            if field_type in {"Instructions", "InstructionsModal"}:
                continue
            options = field.get("options") if isinstance(field.get("options"), list) else []
            if not options:
                continue

            lower = text.lower()
            score = 0.0

            # Synonym / fuzzy map onto an allowed option → strong signal this field owns the value
            mapped = coerce_dropdown_value(text, options)
            if isinstance(mapped, str) and mapped and mapped.lower() != lower:
                # Mapped onto a catalog option (e.g. Auto → Vehicle)
                if any(str(opt).lower() == mapped.lower() for opt in options):
                    score = max(score, 92.0)

            for option in options:
                opt = str(option)
                if opt.lower() == lower:
                    score = max(score, 95.0)
                    continue
                if lower in opt.lower() or opt.lower() in lower:
                    score = max(score, 70.0)
                overlap = _jaccard(_tokens(text), _tokens(opt))
                if overlap >= 0.5:
                    score = max(score, 55.0 + overlap * 30.0)

            score = max(score, score_field_match(str(key), field) * 0.5)
            if score < 50.0:
                continue
            if best is None or score > best[1]:
                best = (str(field_key), score)

        if best:
            used_targets.add(best[0])
            used_sources.add(str(key))
            next_obj[best[0]] = value

    for key, value in incoming.items():
        if key in used_sources:
            continue
        if key in next_obj:
            continue
        next_obj[key] = value

    return next_obj


def coerce_dropdown_value(value: Any, options: list[Any] | None) -> Any:
    """Map free text onto an allowed dropdown / radio option (fuzzy + synonyms)."""
    if not options:
        return value
    if value is None or value == "":
        return value

    if isinstance(value, bool):
        raw = "yes" if value else "no"
    else:
        raw = str(value).strip()
    if not raw:
        return value

    option_strs = [str(opt).strip() for opt in options if opt is not None and str(opt).strip()]
    if not option_strs:
        return value

    lower = raw.lower()
    for option in option_strs:
        if option.lower() == lower:
            return option

    # Synonym buckets → preferred option labels
    synonym_rules: list[tuple[re.Pattern[str], list[str]]] = [
        (re.compile(r"\b(auto|automobile|car|vehicle|motor\s*vehicle)\b", re.I), ["Vehicle", "Auto", "Automobile", "Car"]),
        (re.compile(r"\b(home\s*owner|homeowners?|home\s*insurance|dwelling)\b", re.I), ["Homeowner/Renter", "Homeowners", "Home"]),
        (re.compile(r"\b(renter|renters|tenant)\b", re.I), ["Homeowner/Renter", "Renters", "Renter"]),
        (re.compile(r"\b(life\s*insurance|term\s*life|whole\s*life)\b", re.I), ["Life"]),
        (re.compile(r"\b(health|medical|dental|vision)\b", re.I), ["Health", "Medical", "Dental"]),
        (re.compile(r"\b(bank\s*loan|loan\s*insurance|credit\s*life|cpi|gap\s*insurance)\b", re.I), ["Bank/Loan", "Credit", "Other"]),
        (re.compile(r"\b(mortgage\s*insurance|pmi|mip)\b", re.I), ["Mortgage", "Bank/Loan", "Other"]),
        (re.compile(r"\b(checking)\b", re.I), ["Checking"]),
        (re.compile(r"\b(savings)\b", re.I), ["Savings"]),
        (re.compile(r"\b(yes|y|true|checked|enabled|on)\b", re.I), ["Yes", "Y", "True"]),
        (re.compile(r"\b(no|n|false|unchecked|disabled|off)\b", re.I), ["No", "N", "False"]),
        (re.compile(r"\b(owned|own|owner)\b", re.I), ["Owned", "Own", "Owner"]),
        (re.compile(r"\b(rented|rent|lease|leased)\b", re.I), ["Rented", "Rent", "Leased", "Lease"]),
        (re.compile(r"\b(married)\b", re.I), ["Married"]),
        (re.compile(r"\b(single)\b", re.I), ["Single"]),
        (re.compile(r"\b(personal\s*loan)\b", re.I), ["Personal Loan"]),
        (re.compile(r"\b(student\s*loan)\b", re.I), ["Student Loan"]),
        (re.compile(r"\b(auto\s*loan|car\s*loan)\b", re.I), ["Auto Loan"]),
        (re.compile(r"\b(mortgage|home\s*loan|heloc)\b", re.I), ["Home Equity Loan", "Mortgage", "Line of Credit"]),
    ]
    for pattern, preferred in synonym_rules:
        if not pattern.search(raw):
            continue
        for pref in preferred:
            pref_n = _norm_key(pref)
            for option in option_strs:
                opt_n = _norm_key(option)
                if opt_n == pref_n or pref_n in opt_n or opt_n in pref_n:
                    return option

    for option in option_strs:
        if option.lower() in lower or lower in option.lower():
            return option

    # Token overlap (e.g. "Auto / Vehicle" → "Vehicle")
    raw_tokens = set(_tokens(raw))
    best: tuple[str, float] | None = None
    for option in option_strs:
        opt_tokens = _tokens(option)
        if not opt_tokens:
            continue
        overlap = len(raw_tokens & set(opt_tokens))
        if overlap <= 0:
            continue
        score = overlap / max(len(opt_tokens), 1)
        if best is None or score > best[1]:
            best = (option, score)
    if best and best[1] >= 0.34:
        return best[0]

    return value


def _is_truthy_flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if not isinstance(value, str):
        return None
    n = value.strip().lower()
    if n in {"true", "yes", "y", "1", "checked", "enabled", "on", "active"}:
        return True
    if n in {"false", "no", "n", "0", "unchecked", "disabled", "off", "inactive"}:
        return False
    return None


def coerce_catalog_values(obj: Any, fields: list[dict] | None) -> Any:
    """After placement, coerce Dropdown / Radio / Checkbox values onto form shapes."""
    if not fields or not isinstance(obj, dict):
        return obj

    by_key = {
        str(field.get("key")): field
        for field in fields
        if isinstance(field, dict) and field.get("key")
    }

    next_obj: dict = {}
    for key, value in obj.items():
        field = by_key.get(str(key))
        if not field:
            next_obj[key] = value
            continue

        field_type = str(field.get("type") or "")
        options = field.get("options") if isinstance(field.get("options"), list) else []

        if field_type == "Checkbox":
            flag = _is_truthy_flag(value)
            next_obj[key] = flag if flag is not None else bool(value)
            continue

        # Single-option RadioButtons act like checkboxes in the UI.
        if field_type == "RadioButtons" and len(options) == 1:
            only = str(options[0])
            flag = _is_truthy_flag(value)
            if flag is True:
                next_obj[key] = only
            elif flag is False:
                next_obj[key] = ""
            else:
                next_obj[key] = coerce_dropdown_value(value, options)
            continue

        if field_type in {"Dropdown", "Radio", "RadioButtons", "Select"} and options:
            next_obj[key] = coerce_dropdown_value(value, options)
        elif field_type == "MultiSelect" and options:
            if isinstance(value, list):
                parts = value
            else:
                parts = [part.strip() for part in re.split(r"[,;|/]+", str(value)) if part.strip()]
            resolved = []
            for part in parts:
                mapped = coerce_dropdown_value(part, options)
                resolved.append(mapped if mapped is not None else part)
            # preserve order, unique
            seen: set[str] = set()
            unique: list[Any] = []
            for item in resolved:
                token = str(item)
                if token in seen:
                    continue
                seen.add(token)
                unique.append(item)
            next_obj[key] = unique
        else:
            next_obj[key] = value

    return next_obj


def smart_place_patch(patch: Any, fields: list[dict] | None) -> Any:
    """Deep remap for section patches (objects + card arrays)."""
    if not isinstance(patch, dict):
        return patch
    if not fields:
        return patch

    next_patch: dict = {}
    for key, value in patch.items():
        if isinstance(value, list):
            next_patch[key] = [
                coerce_catalog_values(smart_place_onto_fields(item, fields), fields)
                if isinstance(item, dict)
                else item
                for item in value
            ]
        elif isinstance(value, dict):
            if "text" in value or "files" in value:
                placed = smart_place_onto_fields({key: value}, fields)
                next_patch.update(coerce_catalog_values(placed, fields))
            else:
                next_patch[key] = coerce_catalog_values(
                    smart_place_onto_fields(value, fields),
                    fields,
                )
        else:
            placed = smart_place_onto_fields({key: value}, fields)
            next_patch.update(coerce_catalog_values(placed, fields))

    return next_patch


def remap_extraction_result(result: dict | None, field_catalog: list[dict] | None) -> dict | None:
    """Apply catalog-aware smart placement to an extractor result."""
    if not isinstance(result, dict):
        return result
    patch = result.get("patch")
    if not isinstance(patch, dict):
        return result

    remapped = smart_place_patch(patch, field_catalog)
    next_result = dict(result)
    next_result["patch"] = remapped
    return next_result
