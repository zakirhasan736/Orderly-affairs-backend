"""Build, encrypt, store, and prune daily Mongo user-data backups."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from bson import json_util

from app.backup.collections import BACKUP_COLLECTIONS
from app.backup.crypto_file import decrypt_file, encrypt_file, resolve_backup_key
from app.backup.s3 import upload_backup_to_s3
from app.config import settings
from app.database import db

BACKUP_NAME_RE = re.compile(r"^orderly-backup-\d{8}-\d{6}\.oa1b$")


def _backup_root() -> Path:
    root = Path(settings.BACKUP_ROOT)
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


async def _export_collection(name: str, out_path: Path) -> int:
    """Write one NDJSON file (BSON-extended JSON). Returns document count."""
    count = 0
    collection = db[name]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        cursor = collection.find({})
        async for doc in cursor:
            fh.write(json_util.dumps(doc, separators=(",", ":")))
            fh.write("\n")
            count += 1
    return count


def _add_vault_files(staging: Path) -> int:
    """Copy vault files into staging if enabled. Returns file count."""
    if not settings.BACKUP_INCLUDE_VAULT_FILES:
        return 0
    vault = Path(settings.VAULT_ROOT)
    if not vault.is_absolute():
        vault = Path.cwd() / vault
    if not vault.exists():
        return 0
    dest = staging / "vault_files"
    file_count = 0
    for path in vault.rglob("*"):
        if path.is_file():
            rel = path.relative_to(vault)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            file_count += 1
    return file_count


def _prune_local(root: Path, keep_days: int) -> list[str]:
    """Delete encrypted packages older than retention."""
    if keep_days <= 0:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    removed: list[str] = []
    for path in root.glob("orderly-backup-*.oa1b"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            path.unlink(missing_ok=True)
            sidecar = path.with_suffix(".oa1b.manifest.json")
            sidecar.unlink(missing_ok=True)
            removed.append(path.name)
    return removed


async def run_daily_backup(*, upload_s3: bool | None = None) -> dict[str, Any]:
    """
    Export selected Mongo collections (ciphertext as stored), tar.gz,
    AES-GCM encrypt to BACKUP_ROOT, optionally upload to S3, prune locals.
    """
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    root = _backup_root()
    # Touch key early so misconfig fails before a long export.
    resolve_backup_key()

    counts: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="orderly-backup-") as tmp:
        staging = Path(tmp) / "payload"
        staging.mkdir(parents=True, exist_ok=True)
        mongo_dir = staging / "mongo"
        mongo_dir.mkdir()

        for name in BACKUP_COLLECTIONS:
            out = mongo_dir / f"{name}.ndjson"
            counts[name] = await _export_collection(name, out)

        vault_files = _add_vault_files(staging)

        manifest_plain = {
            "app": settings.APP_NAME,
            "format": "orderly-backup-v1",
            "created_at": started.isoformat(),
            "database": db.name,
            "collections": counts,
            "vault_files_included": bool(settings.BACKUP_INCLUDE_VAULT_FILES),
            "vault_file_count": vault_files,
            "note": (
                "Documents are archived as stored. Encrypted vault fields remain "
                "ciphertext; restore requires AES_256_KEY (and BACKUP_ENCRYPTION_KEY "
                "to open this package)."
            ),
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest_plain, indent=2),
            encoding="utf-8",
        )

        tar_path = Path(tmp) / f"orderly-backup-{stamp}.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(staging, arcname="payload")

        enc_name = f"orderly-backup-{stamp}.oa1b"
        enc_path = root / enc_name
        encrypt_file(tar_path, enc_path)

    digest = _sha256_file(enc_path)
    size = enc_path.stat().st_size

    s3_key = None
    do_s3 = settings.BACKUP_S3_ENABLED if upload_s3 is None else upload_s3
    if do_s3:
        prefix = (settings.BACKUP_S3_PREFIX or "orderly-affairs/backups").strip("/")
        day = started.strftime("%Y/%m/%d")
        s3_key = upload_backup_to_s3(
            enc_path,
            object_key=f"{prefix}/{day}/{enc_name}",
        )

    pruned = _prune_local(root, int(settings.BACKUP_RETENTION_DAYS))

    finished = datetime.now(timezone.utc)
    result = {
        **manifest_plain,
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 2),
        "local_path": str(enc_path),
        "sha256": digest,
        "bytes": size,
        "s3_key": s3_key,
        "pruned_local": pruned,
        "backup_key_source": (
            "BACKUP_ENCRYPTION_KEY"
            if (settings.BACKUP_ENCRYPTION_KEY or "").strip()
            else "AES_256_KEY"
        ),
    }
    manifest_path = enc_path.with_suffix(".oa1b.manifest.json")
    # Sidecar is metadata only (no secrets); safe for ops visibility.
    manifest_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def resolve_backup_package(filename: str) -> Path:
    """Resolve a safe path under BACKUP_ROOT for an .oa1b package."""
    name = Path(filename).name
    if not BACKUP_NAME_RE.match(name):
        raise ValueError("Invalid backup filename")
    root = _backup_root().resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise FileNotFoundError("Backup package not found")
    return path


def list_local_backups() -> dict[str, Any]:
    """List local .oa1b packages newest-first for the admin table."""
    root = _backup_root()
    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("orderly-backup-*.oa1b"), reverse=True):
        if not BACKUP_NAME_RE.match(path.name):
            continue
        try:
            st = path.stat()
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        except OSError:
            continue
        manifest: dict[str, Any] = {}
        sidecar = path.with_suffix(".oa1b.manifest.json")
        if sidecar.is_file():
            try:
                manifest = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = {}
        collections = manifest.get("collections") or {}
        doc_total = sum(int(v) for v in collections.values()) if isinstance(collections, dict) else 0
        items.append(
            {
                "filename": path.name,
                "created_at": manifest.get("created_at") or mtime.isoformat(),
                "bytes": int(manifest.get("bytes") or st.st_size),
                "sha256": manifest.get("sha256"),
                "s3_key": manifest.get("s3_key"),
                "document_count": doc_total,
                "collections": collections if isinstance(collections, dict) else {},
                "vault_file_count": int(manifest.get("vault_file_count") or 0),
                "is_latest": False,
            }
        )
    if items:
        items[0]["is_latest"] = True
    return {
        "backup_root": str(root),
        "backup_enabled": bool(settings.BACKUP_ENABLED),
        "s3_enabled": bool(settings.BACKUP_S3_ENABLED),
        "retention_days": int(settings.BACKUP_RETENTION_DAYS),
        "cron_utc": f"{int(settings.BACKUP_CRON_HOUR):02d}:{int(settings.BACKUP_CRON_MINUTE):02d}",
        "count": len(items),
        "items": items,
        "latest": items[0]["filename"] if items else None,
    }


async def _restore_collection_from_ndjson(name: str, ndjson_path: Path) -> int:
    docs: list[Any] = []
    with ndjson_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            docs.append(json_util.loads(line))
    collection = db[name]
    await collection.delete_many({})
    if not docs:
        return 0
    batch = 500
    for i in range(0, len(docs), batch):
        await collection.insert_many(docs[i : i + batch])
    return len(docs)


def _restore_vault_files(vault_src: Path) -> int:
    if not vault_src.is_dir():
        return 0
    vault = Path(settings.VAULT_ROOT)
    if not vault.is_absolute():
        vault = Path.cwd() / vault
    vault.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in vault_src.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(vault_src)
        target = vault / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return count


async def restore_from_backup(
    filename: str,
    *,
    create_safety_backup: bool = True,
) -> dict[str, Any]:
    """
    Decrypt a local .oa1b package and replace Mongo collections from it.

    Optionally takes a fresh safety snapshot first so a bad restore can be undone.
    """
    package = resolve_backup_package(filename)
    started = datetime.now(timezone.utc)
    safety: dict[str, Any] | None = None
    if create_safety_backup:
        safety = await run_daily_backup(upload_s3=False)

    restored: dict[str, int] = {}
    vault_restored = 0
    with tempfile.TemporaryDirectory(prefix="orderly-restore-") as tmp:
        tmp_path = Path(tmp)
        tar_path = tmp_path / "restore.tar.gz"
        decrypt_file(package, tar_path)
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with tarfile.open(tar_path, "r:gz") as tar:
            # Python 3.12+ supports filter=; older uses default extract.
            try:
                tar.extractall(extract_dir, filter=tarfile.data_filter)
            except (AttributeError, TypeError):
                tar.extractall(extract_dir)

        payload = extract_dir / "payload"
        if not payload.is_dir():
            # Older layouts might extract without nesting quirks
            candidates = list(extract_dir.rglob("mongo"))
            if not candidates:
                raise ValueError("Backup payload missing mongo/ directory")
            payload = candidates[0].parent

        mongo_dir = payload / "mongo"
        if not mongo_dir.is_dir():
            raise ValueError("Backup has no mongo export")

        for ndjson in sorted(mongo_dir.glob("*.ndjson")):
            name = ndjson.stem
            restored[name] = await _restore_collection_from_ndjson(name, ndjson)

        vault_src = payload / "vault_files"
        if vault_src.is_dir():
            vault_restored = _restore_vault_files(vault_src)

    finished = datetime.now(timezone.utc)
    return {
        "restored_from": package.name,
        "created_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 2),
        "collections": restored,
        "document_count": sum(restored.values()),
        "vault_files_restored": vault_restored,
        "safety_backup": safety.get("local_path") if safety else None,
        "safety_backup_filename": Path(safety["local_path"]).name if safety else None,
    }
