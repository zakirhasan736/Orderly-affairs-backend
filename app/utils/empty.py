"""Shared empty-value helpers (no crypto imports)."""


def is_effectively_empty(value) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return True
    if isinstance(value, dict):
        return all(is_effectively_empty(v) for v in value.values())
    if isinstance(value, list):
        return all(is_effectively_empty(v) for v in value)
    return False
