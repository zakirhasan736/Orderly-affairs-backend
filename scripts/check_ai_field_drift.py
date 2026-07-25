#!/usr/bin/env python3
"""Fail if canonical AI fields are missing from section JSON schemas.

Usage (from backend root):
  python scripts/check_ai_field_drift.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.section_field_ssot import (  # noqa: E402
    CANONICAL_SECTION_FIELDS,
    load_section_schema,
    schema_leaf_keys,
)


def main() -> int:
    errors: list[str] = []

    for section_key, fields in CANONICAL_SECTION_FIELDS.items():
        schema = load_section_schema(section_key)
        if not schema:
            errors.append(f"{section_key}: schema not found")
            continue
        keys = schema_leaf_keys(schema)
        for field in fields:
            key = field["key"]
            if key not in keys:
                errors.append(f"{section_key}: schema missing key '{key}'")

    if errors:
        print("AI field drift detected:")
        for line in errors:
            print(f"  - {line}")
        return 1

    print("OK: canonical AI fields present in schemas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
