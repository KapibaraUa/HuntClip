"""Storage retention cleanup and log rotation utilities (plan, Sections 12 & 14)."""

from __future__ import annotations

import contextlib
import gzip
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cliphunter.config import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass
class CleanupReport:
    """Summary of a retention cleanup and log rotation run."""

    retention_days: int
    dry_run: bool
    deleted_files_count: int = 0
    freed_bytes: int = 0
    rotated_logs_count: int = 0
    deleted_paths: list[str] = field(default_factory=list)
    rotated_log_paths: list[str] = field(default_factory=list)

    @property
    def freed_mb(self) -> float:
        return round(self.freed_bytes / (1024 * 1024), 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "retention_days": self.retention_days,
            "dry_run": self.dry_run,
            "deleted_files_count": self.deleted_files_count,
            "freed_bytes": self.freed_bytes,
            "freed_mb": self.freed_mb,
            "rotated_logs_count": self.rotated_logs_count,
            "deleted_paths": self.deleted_paths,
            "rotated_log_paths": self.rotated_log_paths,
        }


def _collect_expired_files(
    directory: Path, cutoff_ts: float, suffixes: tuple[str, ...] | None = None
) -> list[Path]:
    """Returns files in `directory` (recursively) whose mtime is older than `cutoff_ts`."""
    if not directory.exists():
        return []
    expired: list[Path] = []
    for p in directory.rglob("*"):
        if not p.is_file():
            continue
        if suffixes is not None and p.suffix.lower() not in suffixes:
            continue
        try:
            if p.stat().st_mtime < cutoff_ts:
                expired.append(p)
        except OSError:
            continue
    return expired


def rotate_logs(
    logs_dir: Path,
    max_size_bytes: int = 10 * 1024 * 1024,
    keep_archives: int = 10,
    force: bool = False,
    dry_run: bool = False,
) -> list[Path]:
    """Rotates `.jsonl` and `.log` files in `logs_dir` into `.gz` archives when they exceed `max_size_bytes`."""
    if not logs_dir.exists():
        return []

    rotated: list[Path] = []
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    for log_file in sorted(logs_dir.iterdir()):
        if not log_file.is_file():
            continue
        if log_file.suffix.lower() not in (".jsonl", ".log"):
            continue

        size = log_file.stat().st_size
        if size == 0:
            continue
        if not force and size < max_size_bytes:
            continue

        archive_name = f"{log_file.stem}_{ts}{log_file.suffix}.gz"
        archive_path = logs_dir / archive_name
        counter = 1
        while archive_path.exists():
            archive_path = logs_dir / f"{log_file.stem}_{ts}_{counter}{log_file.suffix}.gz"
            counter += 1

        if not dry_run:
            with log_file.open("rb") as f_in, gzip.open(archive_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            # Truncate active log file so writers can continue appending cleanly
            log_file.write_bytes(b"")

        rotated.append(archive_path)
        log.info("Rotated log file %s -> %s (%d bytes)", log_file.name, archive_path.name, size)

    # Prune old compressed log archives beyond keep_archives
    if keep_archives >= 1 and not dry_run:
        archives = sorted(
            logs_dir.glob("*.gz"),
            key=lambda p: (p.stat().st_mtime, p.name),
            reverse=True,
        )
        for old_arch in archives[keep_archives:]:
            with contextlib.suppress(OSError):
                old_arch.unlink()

    return rotated


def cleanup_expired_data(
    settings: Settings | None = None,
    retention_days: int | None = None,
    dry_run: bool = False,
    rotate_log_files: bool = True,
    force_log_rotate: bool = False,
) -> CleanupReport:
    """Purges expired sources, proxies, work artifacts, uploads, and LLM cache older than `retention_days`."""
    app_settings = settings or get_settings()
    days = retention_days if retention_days is not None else app_settings.retention_days
    cutoff_ts = time.time() - (days * 86400.0)
    data_dir = Path(app_settings.data_dir)

    report = CleanupReport(retention_days=days, dry_run=dry_run)

    # 1. Standalone directories: sources, proxies, uploads, live chunks, llm cache
    target_dirs: list[tuple[Path, tuple[str, ...] | None]] = [
        (data_dir / "sources", None),
        (data_dir / "proxies", None),
        (data_dir / "uploads", None),
        (data_dir / "live", (".mp4", ".ts", ".mkv")),
        (data_dir / "cache" / "llm", (".json",)),
    ]

    candidates_to_delete: list[Path] = []
    for folder, suffixes in target_dirs:
        candidates_to_delete.extend(_collect_expired_files(folder, cutoff_ts, suffixes))

    # 2. Job intermediate work directories (`data/jobs/*/work/`)
    # Preserves `state.json` and rendered clips in `data/jobs/*/out/`
    jobs_dir = data_dir / "jobs"
    if jobs_dir.exists():
        for work_dir in jobs_dir.glob("*/work"):
            candidates_to_delete.extend(
                _collect_expired_files(
                    work_dir,
                    cutoff_ts,
                    suffixes=(".mp4", ".mkv", ".webm", ".wav", ".ass"),
                )
            )

    for file_path in candidates_to_delete:
        try:
            fsize = file_path.stat().st_size
            if not dry_run:
                file_path.unlink()
            report.deleted_files_count += 1
            report.freed_bytes += fsize
            report.deleted_paths.append(str(file_path))
        except OSError as exc:
            log.warning("Failed to delete expired file %s: %s", file_path, exc)

    # 3. Rotate logs if requested
    if rotate_log_files:
        logs_dir = data_dir / "logs"
        rotated = rotate_logs(
            logs_dir,
            force=force_log_rotate,
            dry_run=dry_run,
        )
        report.rotated_logs_count = len(rotated)
        report.rotated_log_paths = [str(p) for p in rotated]

    log.info(
        "Cleanup finished (dry_run=%s, days=%d): %d files removed (%.2f MB), %d logs rotated",
        dry_run,
        days,
        report.deleted_files_count,
        report.freed_mb,
        report.rotated_logs_count,
    )
    return report


def get_storage_stats(settings: Settings | None = None) -> dict[str, Any]:
    """Calculates disk usage breakdown across ClipHunter data subdirectories."""
    app_settings = settings or get_settings()
    data_dir = Path(app_settings.data_dir)

    categories = ("sources", "proxies", "jobs", "uploads", "live", "cache", "logs", "backups")
    breakdown: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    total_files = 0

    for cat in categories:
        sub = data_dir / cat
        cat_bytes = 0
        cat_files = 0
        if sub.exists():
            for p in sub.rglob("*"):
                if p.is_file():
                    try:
                        cat_bytes += p.stat().st_size
                        cat_files += 1
                    except OSError:
                        continue
        breakdown[cat] = {
            "files": cat_files,
            "bytes": cat_bytes,
            "mb": round(cat_bytes / (1024 * 1024), 2),
        }
        total_bytes += cat_bytes
        total_files += cat_files

    return {
        "data_dir": str(data_dir),
        "retention_days": app_settings.retention_days,
        "total_files": total_files,
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / (1024 * 1024), 2),
        "breakdown": breakdown,
    }
