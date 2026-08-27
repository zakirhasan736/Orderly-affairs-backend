#!/usr/bin/env python3
"""Read-only export of public vBizMe cards and media. Does not write to source or destination DB/S3."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from migration.api_discovery import ApiDiscovery, envelope_data, extract_profile_id
    from migration.config import ExportConfig, new_run_id, utc_now_iso
    from migration.downloader import download_asset
    from migration.http_client import RateLimiter, ReadOnlyHttpClient
    from migration.manifests import atomic_write_json, load_manifest, slug_dir, verify_local_media
    from migration.media_scanner import discover_media
    from migration.reporting import count_records, media_stats, print_export_summary, print_user_progress
else:
    from .api_discovery import ApiDiscovery, envelope_data, extract_profile_id
    from .config import ExportConfig, new_run_id, utc_now_iso
    from .downloader import download_asset
    from .http_client import RateLimiter, ReadOnlyHttpClient
    from .manifests import atomic_write_json, load_manifest, slug_dir, verify_local_media
    from .media_scanner import discover_media
    from .reporting import count_records, media_stats, print_export_summary, print_user_progress


def load_slugs(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    slugs = []
    for line in lines:
        text = line.strip()
        if text and not text.startswith("#"):
            slugs.append(text)
    return slugs


def select_slugs(slugs: list[str], cfg: ExportConfig) -> list[str]:
    """Return every selected slug. --batch-size only groups progress; it does not stop the run."""
    if cfg.slug_filter:
        return [s for s in slugs if s == cfg.slug_filter]
    start = max(1, cfg.start_from) - 1
    return slugs[start:]


def write_raw(raw_dir: Path, name: str, page: int, payload: Any) -> None:
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in name.strip()) or "section"
    filename = f"{safe}.json" if page == 1 else f"{safe}_page_{page}.json"
    atomic_write_json(raw_dir / filename, payload)


def collect_unresolved(slug: str, endpoints: list[dict[str, Any]], media: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ep in endpoints:
        if ep.get("required") and not ep.get("ok"):
            items.append(
                {
                    "slug": slug,
                    "section": ep.get("name"),
                    "endpoint": ep.get("endpoint"),
                    "json_path": None,
                    "url": ep.get("url"),
                    "error": ep.get("error") or f"HTTP_{ep.get('http_status')}",
                    "http_status": ep.get("http_status"),
                    "attempt_count": ep.get("attempts") if isinstance(ep.get("attempts"), int) else len(ep.get("attempts") or []),
                    "last_attempted": utc_now_iso(),
                }
            )
    for rec in media:
        status = rec.get("download_status")
        if status in {"success", "skipped_external", "discovered"}:
            continue
        items.append(
            {
                "slug": slug,
                "section": rec.get("section"),
                "endpoint": rec.get("section"),
                "json_path": rec.get("json_path"),
                "url": rec.get("original_url"),
                "error": rec.get("error") or status,
                "http_status": rec.get("http_status"),
                "attempt_count": rec.get("attempts") or 0,
                "last_attempted": rec.get("last_attempted") or utc_now_iso(),
            }
        )
    return items


def export_slug(
    slug: str,
    index: int,
    total: int,
    cfg: ExportConfig,
    client: ReadOnlyHttpClient,
) -> dict[str, Any]:
    root = slug_dir(cfg.export_root, slug)
    raw_dir = root / "raw"
    media_dir = root / "media"
    for folder in (raw_dir, root / "normalized", media_dir / "images", media_dir / "videos", media_dir / "documents", media_dir / "other"):
        folder.mkdir(parents=True, exist_ok=True)

    report_path = root / "export_report.json"
    if cfg.resume and report_path.exists() and not cfg.retry_failed:
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        if previous.get("status") == "complete" and not cfg.discover_only:
            print(f"[{index}/{total}] {slug} ........ SKIPPED (already complete)", flush=True)
            return previous

    discovery = ApiDiscovery(client, [cfg.source_api_base, cfg.fallback_api_base])
    endpoints: list[dict[str, Any]] = []
    records: dict[str, int] = {}

    profile_fetch = discovery.fetch_profile(slug)
    profile_fetch["required"] = True
    endpoints.append(
        {
            "name": "profile",
            "endpoint": profile_fetch["endpoint"],
            "url": profile_fetch["url"],
            "http_status": profile_fetch["http_status"],
            "ok": profile_fetch["ok"],
            "required": True,
            "attempts": len(profile_fetch.get("attempts") or []),
        }
    )
    if not profile_fetch["ok"]:
        report = {
            "slug": slug,
            "status": "not_found",
            "api_endpoints_discovered": 1,
            "api_requests_successful": 0,
            "api_requests_failed": 1,
            "records": {},
            "media": media_stats([]),
            "error": f"HTTP_{profile_fetch['http_status']}",
            "run_id": cfg.run_id,
        }
        atomic_write_json(root / "endpoints.json", endpoints)
        atomic_write_json(report_path, report)
        print(f"\n[{index}/{total}] {slug}\n  profile .............. NOT FOUND ({profile_fetch['http_status']})")
        return report

    write_raw(raw_dir, "profile", 1, profile_fetch["payload"])
    card = envelope_data(profile_fetch["payload"])
    atomic_write_json(root / "profile.json", card)
    records["profile"] = 1
    profile_id = extract_profile_id(card)
    if not profile_id:
        raise RuntimeError(f"No profile id in /v/{slug}")

    named_gets = [
        ("post_types", f"/post-types?profile_id={profile_id}", True),
        ("settings", f"/profiles/{profile_id}/settings", True),
        ("announcement", f"/profiles/{profile_id}/announcement", False),
        ("profile_ai_data", f"/profile-ai-data/{profile_id}", False),
    ]
    payloads: dict[str, Any] = {"profile": profile_fetch["payload"]}
    post_types_payload = None
    for name, path, required in named_gets:
        result = discovery.fetch_named(name, path)
        result["required"] = required
        endpoints.append(
            {
                "name": name,
                "endpoint": path,
                "url": result["url"],
                "http_status": result["http_status"],
                "ok": result["ok"],
                "required": required,
                "attempts": len(result.get("attempts") or []),
                "error": None if result["ok"] else f"HTTP_{result['http_status']}",
            }
        )
        if result["ok"]:
            write_raw(raw_dir, name, 1, result["payload"])
            payloads[name] = result["payload"]
            records[name] = count_records(name, result["payload"])
            if name == "post_types":
                post_types_payload = result["payload"]

    sections = discovery.discover_sections(post_types_payload, probe_all=cfg.probe_all)
    atomic_write_json(
        root / "normalized" / "sections_discovered.json",
        {"profile_id": profile_id, "sections": sections},
    )

    for section in sections:
        name = section["name"]
        required = section.get("source") in {"post_types", "StaticLink", "postTypes", "static_links"}

        def _write(section_name: str, page: int, payload: Any, _name=name) -> None:
            write_raw(raw_dir, _name if page == 1 else f"{_name}", page, payload)

        fetched = discovery.fetch_section_pages(profile_id, name, _write)
        ok_pages = [p for p in fetched["pages"] if p["ok"]]
        first = fetched["pages"][0]
        endpoints.append(
            {
                "name": name,
                "endpoint": first["endpoint"],
                "url": first["url"],
                "http_status": first["http_status"],
                "ok": fetched["ok"],
                "required": required,
                "source": section.get("source"),
                "attempts": len(first.get("attempts") or []),
                "error": None if fetched["ok"] else f"HTTP_{first['http_status']}",
            }
        )
        if fetched["ok"]:
            total_items = 0
            merged_items: list[Any] = []
            for page in ok_pages:
                total_items += count_records(name, page["payload"])
                data = envelope_data(page["payload"])
                if isinstance(data, dict) and isinstance(data.get("items"), list):
                    merged_items.extend(data["items"])
                payloads[f"section:{name}"] = page["payload"]
            records[name] = total_items
            atomic_write_json(
                root / "normalized" / f"{''.join(ch if ch.isalnum() or ch in '-._' else '_' for ch in name)}.json",
                {"name": name, "item_count": total_items, "items": merged_items},
            )

    media_records: list[dict[str, Any]] = []
    for section_name, payload in payloads.items():
        section_label = section_name.replace("section:", "")
        scan_payload = card if section_name == "profile" else payload
        for found in discover_media(scan_payload, media_base=cfg.media_base_url, section=section_label):
            media_records.append(
                {
                    "slug": slug,
                    "section": found["section"],
                    "json_path": found["json_path"],
                    "original_url": found["original_url"],
                    "detected_type": found["detected_type"],
                    "original_filename": found["original_filename"],
                    "download_status": "skipped_external" if found["external"] else "discovered",
                    "external": found["external"],
                    "attempts": 0,
                }
            )

    manifest_path = root / "manifest.json"
    previous_manifest = load_manifest(manifest_path) if (cfg.resume or cfg.retry_failed) and manifest_path.exists() else {"media": []}
    previous_by_url_path = {
        (item.get("json_path"), item.get("original_url")): item for item in previous_manifest.get("media") or []
    }
    for rec in media_records:
        prior = previous_by_url_path.get((rec["json_path"], rec["original_url"]))
        if prior:
            rec.update({k: v for k, v in prior.items() if k not in {"section", "json_path", "original_url"}})

    existing_by_sha: dict[str, str] = {}
    existing_by_url: dict[str, dict[str, Any]] = {}
    for rec in media_records:
        if rec.get("sha256") and rec.get("local_path"):
            existing_by_sha[rec["sha256"]] = rec["local_path"]
        if rec.get("original_url"):
            existing_by_url[rec["original_url"]] = rec

    downloadable = [r for r in media_records if not r.get("external")]
    if not cfg.discover_only:
        to_fetch = []
        for rec in downloadable:
            if cfg.retry_failed and rec.get("download_status") == "success":
                continue
            if cfg.resume and rec.get("download_status") == "success" and rec.get("local_path"):
                continue
            to_fetch.append(rec)

        def _job(record: dict[str, Any]) -> dict[str, Any]:
            try:
                result = download_asset(
                    client,
                    url=record["original_url"],
                    dest_root=root,
                    detected_type=record.get("detected_type") or "other",
                    max_bytes=cfg.max_download_bytes,
                    existing_by_sha=existing_by_sha,
                    existing_by_url=existing_by_url,
                )
                merged = {**record, **result, "last_attempted": utc_now_iso()}
                return merged
            except Exception as exc:  # noqa: BLE001
                return {
                    **record,
                    "download_status": "failed",
                    "error": str(exc),
                    "last_attempted": utc_now_iso(),
                }

        if to_fetch:
            print(f"  Downloading {len(to_fetch)} media file(s) for {slug} ...", flush=True)
            with ThreadPoolExecutor(max_workers=cfg.download_concurrency) as pool:
                futures = {pool.submit(_job, rec): rec for rec in to_fetch}
                for future in as_completed(futures):
                    updated = future.result()
                    key = (updated.get("json_path"), updated.get("original_url"))
                    for rec in media_records:
                        if (rec.get("json_path"), rec.get("original_url")) == key:
                            rec.update(updated)
                            break
                    if updated.get("sha256") and updated.get("local_path"):
                        existing_by_sha[updated["sha256"]] = updated["local_path"]
                    existing_by_url[updated["original_url"]] = updated

    manifest = {
        "slug": slug,
        "profile_id": profile_id,
        "run_id": cfg.run_id,
        "media": media_records,
    }
    atomic_write_json(manifest_path, manifest)
    atomic_write_json(root / "endpoints.json", endpoints)

    stats = media_stats(media_records)
    unresolved = collect_unresolved(slug, endpoints, media_records if not cfg.discover_only else [])
    api_ok = sum(1 for e in endpoints if e.get("ok"))
    api_fail = sum(1 for e in endpoints if e.get("required") and not e.get("ok"))
    status = "discover_only" if cfg.discover_only else ("complete" if not unresolved else "incomplete")
    report = {
        "slug": slug,
        "profile_id": profile_id,
        "run_id": cfg.run_id,
        "api_endpoints_discovered": len(endpoints),
        "api_requests_successful": api_ok,
        "api_requests_failed": api_fail,
        "records": records,
        "media": stats,
        "unresolved_count": len(unresolved),
        "status": status,
        "finished_at": utc_now_iso(),
    }
    atomic_write_json(report_path, report)
    print_user_progress(index, total, slug, records, stats, status)
    return {**report, "unresolved": unresolved}


def verify_exports(cfg: ExportConfig, slugs: list[str]) -> int:
    failures = 0
    for slug in slugs:
        root = slug_dir(cfg.export_root, slug)
        manifest_path = root / "manifest.json"
        if not manifest_path.exists():
            print(f"{slug}: FAILED (no manifest)")
            failures += 1
            continue
        manifest = load_manifest(manifest_path)
        problems = verify_local_media(manifest, root)
        if problems:
            print(f"{slug}: FAILED ({len(problems)} file issues)")
            failures += 1
        else:
            print(f"{slug}: VERIFIED")
    print("FAILED" if failures else "VERIFIED")
    return 1 if failures else 0


def log_event(log_path: Path, payload: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export public vBizMe cards (read-only)")
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--verify", "--verify-only", dest="verify", action="store_true")
    parser.add_argument("--slug", default=None)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="How many slugs to group in a progress report. The run still continues through every slug.",
    )
    parser.add_argument("--start-from", type=int, default=1)
    parser.add_argument(
        "--probe-all",
        action="store_true",
        help="Also GET known frontend section names that were not listed in /post-types",
    )
    args = parser.parse_args(argv)

    run_id = new_run_id()
    cfg = ExportConfig(
        discover_only=args.discover_only,
        resume=args.resume or args.retry_failed,
        retry_failed=args.retry_failed,
        verify_only=args.verify,
        batch_size=args.batch_size,
        start_from=args.start_from,
        slug_filter=args.slug,
        probe_all=args.probe_all,
        run_id=run_id,
    )
    cfg.export_root.mkdir(parents=True, exist_ok=True)
    slugs = select_slugs(load_slugs(cfg.slugs_file), cfg)
    if not slugs:
        print("No slugs selected.")
        return 1
    batch_size = cfg.batch_size if cfg.batch_size and cfg.batch_size > 0 else len(slugs)
    print(
        f"Starting export of {len(slugs)} slug(s), one card at a time, "
        f"in batches of {batch_size}. Downloads enabled. Run continues until the last slug.",
        flush=True,
    )

    run_doc = {
        "migration_run_id": run_id,
        "started_at": utc_now_iso(),
        "mode": "verify" if cfg.verify_only else ("discover" if cfg.discover_only else "export"),
        "source_api_base": cfg.source_api_base,
        "fallback_api_base": cfg.fallback_api_base,
        "slugs": slugs,
        "read_only": True,
    }
    atomic_write_json(cfg.export_root / "migration_run.json", run_doc)

    if cfg.verify_only:
        return verify_exports(cfg, slugs)

    limiter = RateLimiter(cfg.max_requests_per_minute)
    client = ReadOnlyHttpClient(user_agent=cfg.user_agent, timeout=cfg.timeout_seconds, rate_limiter=limiter)
    log_path = cfg.export_root / "logs" / f"{run_id}.jsonl"
    all_unresolved: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    try:
        for offset in range(0, len(slugs), batch_size):
            batch = slugs[offset : offset + batch_size]
            batch_id = (offset // batch_size) + 1
            print(
                f"\n================================================\nBATCH {batch_id} START "
                f"({offset + 1}-{offset + len(batch)} of {len(slugs)})\n================",
                flush=True,
            )
            for i, slug in enumerate(batch, start=offset + 1):
                print(f"\n>>> [{i}/{len(slugs)}] starting {slug}", flush=True)
                log_event(log_path, {"migration_run_id": run_id, "batch_id": batch_id, "slug": slug, "status": "started"})
                try:
                    report = export_slug(slug, i, len(slugs), cfg, client)
                    reports.append(report)
                    all_unresolved.extend(report.get("unresolved") or [])
                    log_event(
                        log_path,
                        {
                            "migration_run_id": run_id,
                            "batch_id": batch_id,
                            "slug": slug,
                            "status": report.get("status"),
                            "unresolved": report.get("unresolved_count", 0),
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    err = {
                        "slug": slug,
                        "section": None,
                        "endpoint": None,
                        "json_path": None,
                        "url": None,
                        "error": str(exc),
                        "http_status": None,
                        "attempt_count": 1,
                        "last_attempted": utc_now_iso(),
                        "trace": traceback.format_exc(limit=8),
                    }
                    all_unresolved.append(err)
                    reports.append({"slug": slug, "status": "incomplete", "error": str(exc), "media": media_stats([])})
                    print(f"\n[{i}/{len(slugs)}] {slug}\n  ERROR {exc}", flush=True)
                    log_event(log_path, {"migration_run_id": run_id, "slug": slug, "status": "error", "error": str(exc)})
                atomic_write_json(
                    cfg.export_root / "progress.json",
                    {
                        "migration_run_id": run_id,
                        "current": i,
                        "total": len(slugs),
                        "current_slug": slug,
                        "batch_id": batch_id,
                        "finished_slugs": i,
                        "updated_at": utc_now_iso(),
                    },
                )
            remaining = len(slugs) - (offset + len(batch))
            print(
                f"\n================================================\nBATCH {batch_id} COMPLETE\n"
                f"Continuing automatically. Remaining slugs: {remaining}\n================",
                flush=True,
            )
    finally:
        client.close()

    summary = {
        "migration_run_id": run_id,
        "finished_at": utc_now_iso(),
        "slugs_attempted": len(slugs),
        "users_discovered": sum(1 for r in reports if r.get("status") not in {"not_found"}),
        "users_not_found": sum(1 for r in reports if r.get("status") == "not_found"),
        "api_records": sum(sum((r.get("records") or {}).values()) for r in reports),
        "media_discovered": sum((r.get("media") or {}).get("discovered", 0) for r in reports),
        "unique_media": sum((r.get("media") or {}).get("unique_files", 0) for r in reports),
        "images": sum((r.get("media") or {}).get("images", 0) for r in reports),
        "videos": sum((r.get("media") or {}).get("videos", 0) for r in reports),
        "documents": sum((r.get("media") or {}).get("documents", 0) for r in reports),
        "other": sum((r.get("media") or {}).get("other", 0) for r in reports),
        "downloaded": sum((r.get("media") or {}).get("downloaded", 0) for r in reports),
        "failed": sum((r.get("media") or {}).get("failed", 0) for r in reports),
        "duplicate_references": sum((r.get("media") or {}).get("duplicate_references", 0) for r in reports),
        "bytes": sum((r.get("media") or {}).get("bytes", 0) for r in reports),
        "complete_users": sum(1 for r in reports if r.get("status") == "complete"),
        "incomplete_users": sum(1 for r in reports if r.get("status") in {"incomplete", "not_found"}),
        "unresolved_count": len(all_unresolved),
        "reports": [{k: v for k, v in r.items() if k != "unresolved"} for r in reports],
    }
    atomic_write_json(cfg.export_root / "export_summary.json", summary)
    atomic_write_json(cfg.export_root / "unresolved.json", all_unresolved)
    run_doc["finished_at"] = utc_now_iso()
    run_doc["unresolved_count"] = len(all_unresolved)
    atomic_write_json(cfg.export_root / "migration_run.json", run_doc)
    print_export_summary(summary)
    return 0 if not all_unresolved else 2


if __name__ == "__main__":
    sys.exit(main())
