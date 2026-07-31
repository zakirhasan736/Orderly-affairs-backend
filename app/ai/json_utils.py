# app/ai/json_utils.py

import json
import re


def _strip_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned.startswith("```"):
        return cleaned

    lines = cleaned.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _clean_jsonish(text: str) -> str:
    cleaned = text.strip()
    # Remove common trailing commas before } or ]
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    # Normalize smart quotes that sometimes leak into model output
    cleaned = cleaned.replace("“", '"').replace("”", '"').replace("’", "'")
    return cleaned


def _repair_truncated_json(text: str) -> str | None:
    candidate = text.rstrip()
    if not candidate:
        return None

    if not candidate.endswith("}"):
        last_brace = candidate.rfind("}")
        if last_brace > 0:
            candidate = candidate[: last_brace + 1]

    open_braces = candidate.count("{") - candidate.count("}")
    open_brackets = candidate.count("[") - candidate.count("]")

    if open_braces > 0:
        candidate += "}" * open_braces
    if open_brackets > 0:
        candidate += "]" * open_brackets

    return candidate


def parse_llm_json(raw_text: str | None, fallback: dict | None = None) -> dict:
    text = _clean_jsonish(_strip_fences(raw_text or ""))

    if not text:
        if fallback is not None:
            return fallback
        raise RuntimeError("Empty LLM JSON response")

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    repaired = _repair_truncated_json(text)
    if repaired:
        try:
            parsed = json.loads(_clean_jsonish(repaired))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            parsed = json.loads(_clean_jsonish(match.group(0)))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    if fallback is not None:
        return fallback

    raise RuntimeError("Invalid LLM JSON response")


# Compatibility alias
parse_gemini_json = parse_llm_json
