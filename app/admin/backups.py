"""Admin API — list / run / restore encrypted daily backups."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.admin.audit import log_admin_action
from app.admin.deps import require_admin, require_system_owner
from app.admin.permissions import user_has_area
from app.backup.service import list_local_backups, restore_from_backup, run_daily_backup

admin_backups_router = APIRouter(prefix="/admin/backups", tags=["admin-backups"])


class RunBackupRequest(BaseModel):
    upload_s3: Optional[bool] = None


class RestoreBackupRequest(BaseModel):
    """Destructive: replaces Mongo collections from the chosen package."""

    confirm: Literal["RESTORE"] = Field(
        ...,
        description='Must be the exact string "RESTORE"',
    )
    create_safety_backup: bool = True


async def _require_backups_read(request: Request, authorization: str | None) -> dict:
    admin = await require_admin(request, authorization)
    user = admin.get("user") or {}
    merged = {
        **user,
        "admin_role": admin.get("admin_role"),
        "admin_areas": admin.get("admin_areas"),
    }
    if not user_has_area(merged, "backups"):
        raise HTTPException(403, "No access to area: backups")
    return admin


async def _require_backups_write(request: Request, authorization: str | None) -> dict:
    """Run / restore — Super Admin only."""
    admin = await require_system_owner(request, authorization)
    return admin


@admin_backups_router.get("")
async def list_backups(
    request: Request,
    authorization: str | None = Header(default=None),
):
    await _require_backups_read(request, authorization)
    return list_local_backups()


@admin_backups_router.post("/run")
async def run_backup_now(
    body: RunBackupRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await _require_backups_write(request, authorization)
    try:
        result = await run_daily_backup(upload_s3=body.upload_s3)
    except Exception as exc:
        raise HTTPException(500, f"Backup failed: {exc}") from exc

    await log_admin_action(
        admin.get("email") or "",
        "backup.run",
        target=result.get("local_path"),
        meta={
            "sha256": result.get("sha256"),
            "bytes": result.get("bytes"),
            "s3_key": result.get("s3_key"),
            "document_count": sum((result.get("collections") or {}).values()),
        },
    )
    return result


@admin_backups_router.post("/{filename}/restore")
async def restore_backup(
    filename: str,
    body: RestoreBackupRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    admin = await _require_backups_write(request, authorization)
    if body.confirm != "RESTORE":
        raise HTTPException(400, 'confirm must be exactly "RESTORE"')

    try:
        result = await restore_from_backup(
            filename,
            create_safety_backup=body.create_safety_backup,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Restore failed: {exc}") from exc

    await log_admin_action(
        admin.get("email") or "",
        "backup.restore",
        target=filename,
        meta={
            "document_count": result.get("document_count"),
            "collections": result.get("collections"),
            "safety_backup_filename": result.get("safety_backup_filename"),
            "vault_files_restored": result.get("vault_files_restored"),
        },
    )
    return result
