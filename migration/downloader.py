from __future__ import annotations

from pathlib import Path
from typing import Any

from .checksum import sha256_bytes, sha256_file
from .http_client import ReadOnlyHttpClient
from .media_scanner import classify_type, original_filename

HTML_SNIFF = (b"<!doctype html", b"<html", b"<head")


def _is_html_error(content_type: str | None, body: bytes) -> bool:
    ctype = (content_type or "").lower()
    if "text/html" in ctype:
        prefix = body[:200].lstrip().lower()
        return any(prefix.startswith(sig) for sig in HTML_SNIFF)
    prefix = body[:200].lstrip().lower()
    return prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html")


def folder_for_type(detected: str) -> str:
    if detected == "image":
        return "images"
    if detected == "video":
        return "videos"
    if detected == "document":
        return "documents"
    return "other"


def download_asset(
    client: ReadOnlyHttpClient,
    *,
    url: str,
    dest_root: Path,
    detected_type: str,
    max_bytes: int,
    existing_by_sha: dict[str, str],
    existing_by_url: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    cached = existing_by_url.get(url)
    if cached and cached.get("download_status") == "success" and cached.get("local_path"):
        path = dest_root / cached["local_path"]
        if path.exists() and (not cached.get("sha256") or sha256_file(path) == cached["sha256"]):
            return {**cached, "resumed": True}

    response, attempts = client.get(url, stream=True, headers={"Accept": "*/*"})
    status = response.status_code
    content_type = response.headers.get("Content-Type")
    content_length = response.headers.get("Content-Length")
    result: dict[str, Any] = {
        "original_url": url,
        "http_status": status,
        "content_type": content_type,
        "attempts": len(attempts),
        "attempt_log": attempts,
        "original_filename": original_filename(url),
    }
    if status == 404:
        result.update(download_status="source_missing", error="SOURCE_MISSING")
        return result
    if status in {401, 403}:
        result.update(download_status="failed", error=f"HTTP_{status}")
        return result
    if status >= 400:
        result.update(download_status="failed", error=f"HTTP_{status}")
        return result

    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(1024 * 64):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                result.update(download_status="failed", error="FILE_TOO_LARGE", size_bytes=total)
                return result
            chunks.append(chunk)
    finally:
        response.close()

    body = b"".join(chunks)
    if not body:
        result.update(download_status="failed", error="EMPTY_BODY", size_bytes=0)
        return result
    if _is_html_error(content_type, body):
        result.update(download_status="failed", error="HTML_ERROR_PAGE", size_bytes=len(body))
        return result
    if content_length:
        try:
            expected = int(content_length)
            if expected > 0 and expected != len(body):
                result.update(
                    download_status="failed",
                    error="CONTENT_LENGTH_MISMATCH",
                    size_bytes=len(body),
                    expected_size=expected,
                )
                return result
        except ValueError:
            pass

    digest = sha256_bytes(body)
    detected = classify_type(url, content_type) if detected_type in {"other", "external"} else detected_type
    if detected == "external":
        detected = classify_type(url, content_type)
    ext = Path(original_filename(url)).suffix.lower()
    if not ext or len(ext) > 8:
        subtype = (content_type or "").split("/")[-1].split(";")[0].strip().lower()
        ext = f".{subtype}" if subtype and subtype.isalnum() else ".bin"
    rel = Path("media") / folder_for_type(detected) / f"{digest}{ext}"
    dest = dest_root / rel
    if digest in existing_by_sha:
        rel = Path(existing_by_sha[digest])
        dest = dest_root / rel
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(body)
        existing_by_sha[digest] = str(rel).replace("\\", "/")

    result.update(
        {
            "download_status": "success",
            "detected_type": detected,
            "size_bytes": dest.stat().st_size,
            "sha256": digest,
            "local_path": str(rel).replace("\\", "/"),
            "duplicate_binary": digest in existing_by_sha and existing_by_url.get(url, {}).get("sha256") != digest,
        }
    )
    return result
