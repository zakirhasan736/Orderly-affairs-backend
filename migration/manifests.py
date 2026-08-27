from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .checksum import sha256_file


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def slug_dir(export_root: Path, slug: str) -> Path:
    safe = slug.replace("/", "_").replace("\\", "_").replace(":", "_")
    return export_root / "slugs" / safe


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"slug": "", "media": []}
    return read_json(path)


def upsert_media_record(manifest: dict[str, Any], record: dict[str, Any]) -> None:
    media = manifest.setdefault("media", [])
    key = (record.get("json_path"), record.get("original_url"))
    for index, existing in enumerate(media):
        if (existing.get("json_path"), existing.get("original_url")) == key:
            media[index] = {**existing, **record}
            return
    media.append(record)


def verify_local_media(manifest: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for item in manifest.get("media") or []:
        if item.get("download_status") != "success":
            continue
        rel = item.get("local_path")
        if not rel:
            failures.append({**item, "verify_error": "missing_local_path"})
            continue
        path = root / rel
        if not path.exists():
            failures.append({**item, "verify_error": "file_missing"})
            continue
        size = path.stat().st_size
        if item.get("size_bytes") not in (None, size):
            failures.append({**item, "verify_error": "size_mismatch", "actual_size": size})
            continue
        digest = sha256_file(path)
        if item.get("sha256") and item["sha256"] != digest:
            failures.append({**item, "verify_error": "checksum_mismatch", "actual_sha256": digest})
    return failures
