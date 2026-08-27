from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .config import DEDICATED_SECTION_NAMES, PROBE_SECTION_NAMES
from .http_client import ReadOnlyHttpClient


def envelope_data(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload and ("success" in payload or "error" in payload):
        return payload.get("data")
    return payload


def extract_profile_id(card: Any) -> str | None:
    if not isinstance(card, dict):
        return None
    profile = card.get("profile") if isinstance(card.get("profile"), dict) else card
    for key in ("id", "profile_id", "profileId"):
        value = profile.get(key) if isinstance(profile, dict) else None
        if value:
            return str(value)
    return None


def _safe_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in name.strip())
    return cleaned.strip("_") or "section"


def _with_page(url: str, page: int) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))


def pagination_pages(payload: Any) -> list[int]:
    node = payload if isinstance(payload, dict) else {}
    data = node.get("data") if isinstance(node.get("data"), dict) else node
    last = None
    for key in ("last_page", "lastPage", "total_pages"):
        if isinstance(data, dict) and data.get(key):
            last = int(data[key])
            break
    pagination = node.get("pagination") if isinstance(node.get("pagination"), dict) else None
    if pagination and pagination.get("last_page"):
        last = int(pagination["last_page"])
    if last and last > 1:
        return list(range(2, last + 1))
    return []


class ApiDiscovery:
    def __init__(self, client: ReadOnlyHttpClient, api_bases: list[str]) -> None:
        self.client = client
        self.api_bases = [base.rstrip("/") for base in api_bases if base]

    def get_json(self, path_or_url: str) -> tuple[int, Any, str, list[dict[str, Any]]]:
        errors: list[tuple[int, str, list[dict[str, Any]]]] = []
        urls = [path_or_url] if path_or_url.startswith("http") else [f"{base}{path_or_url}" for base in self.api_bases]
        for url in urls:
            response, attempts = self.client.get(url)
            body: Any
            try:
                body = response.json()
            except ValueError:
                body = {"_non_json": True, "text": response.text[:2000]}
            if response.status_code < 400:
                return response.status_code, body, url, attempts
            errors.append((response.status_code, url, attempts))
            if response.status_code not in {404, 400}:
                return response.status_code, body, url, attempts
        status, url, attempts = errors[-1]
        return status, None, url, attempts

    def fetch_profile(self, slug: str) -> dict[str, Any]:
        status, payload, url, attempts = self.get_json(f"/v/{slug}")
        return {
            "name": "profile",
            "endpoint": f"/v/{slug}",
            "url": url,
            "http_status": status,
            "payload": payload,
            "attempts": attempts,
            "ok": status == 200 and payload is not None,
        }

    def fetch_named(self, name: str, path: str) -> dict[str, Any]:
        status, payload, url, attempts = self.get_json(path)
        return {
            "name": name,
            "endpoint": path,
            "url": url,
            "http_status": status,
            "payload": payload,
            "attempts": attempts,
            "ok": status == 200 and payload is not None,
        }

    def discover_sections(self, post_types_payload: Any, *, probe_all: bool = False) -> list[dict[str, str]]:
        data = envelope_data(post_types_payload) or {}
        sections: list[dict[str, str]] = []
        seen: set[str] = set()
        if isinstance(data, dict):
            for group_key in ("post_types", "postTypes", "StaticLink", "static_links"):
                items = data.get(group_key) or []
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or item.get("title") or item.get("slug") or "")
                    if not name.strip():
                        continue
                    lowered = name.strip().lower()
                    if lowered in {"home", "public cards", "public-cards"}:
                        continue
                    if lowered in seen:
                        continue
                    seen.add(lowered)
                    sections.append(
                        {
                            "name": name,
                            "title": str(item.get("title") or name),
                            "slug": str(item.get("slug") or ""),
                            "source": group_key,
                        }
                    )
        extras = DEDICATED_SECTION_NAMES + (PROBE_SECTION_NAMES if probe_all else ())
        for probe in extras:
            if probe.lower() not in seen:
                sections.append({"name": probe, "title": probe, "slug": "", "source": "dedicated" if probe in DEDICATED_SECTION_NAMES else "probe"})
                seen.add(probe.lower())
        return sections

    def fetch_section_pages(self, profile_id: str, section_name: str, write_raw) -> dict[str, Any]:
        encoded = requests_quote(section_name)
        path = f"/dynamic-section/{encoded}?profile_id={profile_id}"
        first = self.fetch_named(section_name, path)
        pages = [first]
        if first["ok"]:
            write_raw(section_name, 1, first["payload"])
            for page in pagination_pages(first["payload"]):
                paged_path = _with_page(path, page)
                nxt = self.fetch_named(f"{section_name}_page_{page}", paged_path)
                pages.append(nxt)
                if nxt["ok"]:
                    write_raw(section_name, page, nxt["payload"])
        return {"name": section_name, "pages": pages, "ok": first["ok"]}


def requests_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
