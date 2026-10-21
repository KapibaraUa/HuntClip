"""System maintenance endpoints: storage usage, database backups, and data/log retention cleanup."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from cliphunter.config import MediaRes, Settings, ThinkingLevel
from cliphunter.maintenance.backup import backup_database, list_backups
from cliphunter.maintenance.cleanup import cleanup_expired_data, get_storage_stats
from cliphunter.worker.tasks import task_manager

router = APIRouter(prefix="/api/system", tags=["system"])


class CleanupRequest(BaseModel):
    retention_days: int | None = None
    dry_run: bool = False
    rotate_logs: bool = True
    force_log_rotate: bool = False


class BackupRequest(BaseModel):
    keep_last: int = 7


class SystemSettingsPayload(BaseModel):
    gemini_model: str | None = None
    thinking_level: ThinkingLevel | None = None
    media_res_scan: MediaRes | None = None
    media_res_refine: MediaRes | None = None
    refine_fps: float | None = None
    max_cost_usd_per_job: float | None = None
    confirm_above_usd: float | None = None
    auto_render: bool | None = None
    subtitles: bool | None = None
    capcut_export: bool | None = None
    use_nvenc: bool | None = None
    retention_days: int | None = None


def _settings_to_dict(s: Settings) -> dict[str, Any]:
    return {
        "gemini_model": s.gemini_model,
        "thinking_level": s.thinking_level,
        "media_res_scan": s.media_res_scan,
        "media_res_refine": s.media_res_refine,
        "refine_fps": s.refine_fps,
        "chunk_s": s.chunk_s,
        "overlap_s": s.overlap_s,
        "clip_min_s": s.clip_min_s,
        "clip_max_s": s.clip_max_s,
        "max_cost_usd_per_job": s.max_cost_usd_per_job,
        "confirm_above_usd": s.confirm_above_usd,
        "auto_render": s.auto_render,
        "subtitles": s.subtitles,
        "capcut_export": s.capcut_export,
        "use_nvenc": s.use_nvenc,
        "retention_days": s.retention_days,
        "gemini_api_key_configured": bool(s.gemini_api_key.get_secret_value().strip()),
        "telegram_token_configured": bool(s.telegram_token.get_secret_value().strip()),
    }


@router.get("/settings")
async def get_system_settings() -> dict[str, Any]:
    """Returns current model, budget, rendering, and storage settings (with secrets masked)."""
    return _settings_to_dict(task_manager.settings)


@router.put("/settings")
async def update_system_settings(payload: SystemSettingsPayload) -> dict[str, Any]:
    """Updates runtime settings for model parameters, budget limits, and rendering flags."""
    updates = payload.model_dump(exclude_none=True)
    new_settings = task_manager.settings.model_copy(update=updates)
    task_manager.settings = new_settings
    return _settings_to_dict(new_settings)


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
