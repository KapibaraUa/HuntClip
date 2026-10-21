"""System maintenance endpoints: storage usage, database backups, and data/log retention cleanup."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cliphunter.config import Settings
from cliphunter.maintenance.backup import backup_database, list_backups
from cliphunter.maintenance.cleanup import cleanup_expired_data, get_storage_stats

router = APIRouter(prefix="/api/system", tags=["system"])


class CleanupRequest(BaseModel):
    retention_days: int | None = None
    dry_run: bool = False
    rotate_logs: bool = True
    force_log_rotate: bool = False


class BackupRequest(BaseModel):
    keep_last: int = 7


@router.get("/storage")
async def get_system_storage() -> dict[str, Any]:
    """Returns disk usage statistics across data directories and available DB backups."""
    settings = Settings()
    stats = get_storage_stats(settings=settings)
    backups = list_backups(settings=settings)
    return {
        **stats,
        "backups_count": len(backups),
        "backups": backups,
    }


@router.post("/backup")
async def trigger_db_backup(req: BackupRequest | None = None) -> dict[str, Any]:
    """Creates a compressed snapshot of the database and rotates old backups."""
    settings = Settings()
    keep = req.keep_last if req is not None else 7
    try:
        archive = backup_database(settings=settings, keep_last=keep)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database backup failed: {exc}") from exc

    return {
        "name": archive.name,
        "path": str(archive),
        "size_bytes": archive.stat().st_size,
        "backups": list_backups(settings=settings),
    }


@router.post("/cleanup")
async def trigger_data_cleanup(req: CleanupRequest | None = None) -> dict[str, Any]:
    """Purges expired videos, proxies, work artifacts, and rotates logs based on retention policy."""
    settings = Settings()
    payload = req or CleanupRequest()
    report = cleanup_expired_data(
        settings=settings,
        retention_days=payload.retention_days,
        dry_run=payload.dry_run,
        rotate_log_files=payload.rotate_logs,
        force_log_rotate=payload.force_log_rotate,
    )
    return report.to_dict()
