from __future__ import annotations

from typing import Any


def count_records(section: str, payload: Any) -> int:
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return len(data["items"])
    if isinstance(data, list):
        return len(data)
    if data is None:
        return 0
    return 1


def media_stats(records: list[dict[str, Any]]) -> dict[str, int]:
    stats = {
        "discovered": len(records),
        "images": 0,
        "videos": 0,
        "documents": 0,
        "audio": 0,
        "other": 0,
        "downloaded": 0,
        "failed": 0,
        "source_missing": 0,
        "external_skipped": 0,
        "unique_files": 0,
        "duplicate_references": 0,
        "bytes": 0,
    }
    unique = set()
    for item in records:
        detected = item.get("detected_type") or "other"
        type_key = {"image": "images", "video": "videos", "document": "documents", "audio": "audio"}.get(detected, "other")
        if item.get("external") and item.get("download_status") == "skipped_external":
            stats["external_skipped"] += 1
        else:
            stats[type_key] += 1
        status = item.get("download_status")
        if status == "success":
            stats["downloaded"] += 1
            if item.get("sha256"):
                if item["sha256"] in unique:
                    stats["duplicate_references"] += 1
                else:
                    unique.add(item["sha256"])
                    stats["bytes"] += int(item.get("size_bytes") or 0)
        elif status == "source_missing":
            stats["source_missing"] += 1
            stats["failed"] += 1
        elif status in {"failed", "pending"}:
            if status == "failed":
                stats["failed"] += 1
    stats["unique_files"] = len(unique)
    return stats


def print_user_progress(
    index: int,
    total: int,
    slug: str,
    records: dict[str, int],
    media: dict[str, int],
    status: str,
) -> None:
    print(f"\n[{index}/{total}] {slug}", flush=True)
    for name, count in records.items():
        if count == 0 and name not in {"profile", "post_types", "settings"}:
            continue
        dots = "." * max(2, 22 - len(name))
        print(f"  {name} {dots} {count}", flush=True)
    print(f"  Media discovered ..... {media.get('discovered', 0)}", flush=True)
    print(f"  Images ................ {media.get('images', 0)}", flush=True)
    print(f"  Videos ................ {media.get('videos', 0)}", flush=True)
    print(f"  Documents ............. {media.get('documents', 0)}", flush=True)
    print(
        f"  Downloaded ............ {media.get('downloaded', 0)}/{media.get('discovered', 0) - media.get('external_skipped', 0)}",
        flush=True,
    )
    print(f"  Failed ................ {media.get('failed', 0)}", flush=True)
    print(f"  Export status ......... {status.upper()}", flush=True)


def print_export_summary(summary: dict[str, Any]) -> None:
    unresolved = summary.get("unresolved_count", 0)
    print("\n================================================")
    print("VBIZME EXPORT SUMMARY")
    print("=====================")
    print(f"Users/slugs attempted:          {summary.get('slugs_attempted', 0)}")
    print(f"Users successfully discovered:  {summary.get('users_discovered', 0)}")
    print(f"Users not found:                {summary.get('users_not_found', 0)}")
    print(f"API records discovered:         {summary.get('api_records', 0)}")
    print(f"Media references discovered:    {summary.get('media_discovered', 0)}")
    print(f"Unique media files:             {summary.get('unique_media', 0)}")
    print(f"Images:                         {summary.get('images', 0)}")
    print(f"Videos:                         {summary.get('videos', 0)}")
    print(f"Documents/PDFs:                 {summary.get('documents', 0)}")
    print(f"Other files:                    {summary.get('other', 0)}")
    print(f"Downloaded successfully:        {summary.get('downloaded', 0)}")
    print(f"Failed downloads:               {summary.get('failed', 0)}")
    print(f"Duplicate references:           {summary.get('duplicate_references', 0)}")
    print(f"Total bytes downloaded:         {_fmt_bytes(summary.get('bytes', 0))}")
    print(f"Complete users:                 {summary.get('complete_users', 0)}")
    print(f"Incomplete users:               {summary.get('incomplete_users', 0)}")
    print(f"UNRESOLVED ITEMS:               {unresolved}")
    print("================================================")
    if unresolved:
        print(f"EXPORT COMPLETE WITH {unresolved} UNRESOLVED ASSETS")
    else:
        print("EXPORT VERIFIED — 0 UNRESOLVED ASSETS")


def _fmt_bytes(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{num} B"
