"""Database backup, restore, and snapshot rotation utilities (plan, Phase 7)."""

from __future__ import annotations

import gzip
import logging
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cliphunter.config import Settings, get_settings

log = logging.getLogger(__name__)


def resolve_sqlite_path(database_url: str) -> Path | None:
    """Extracts the filesystem Path from a SQLite SQLAlchemy URL, or None if not SQLite."""
    if not database_url.lower().startswith("sqlite"):
        return None

    # Format: sqlite+aiosqlite:///relative/path.db or sqlite+aiosqlite:///C:/abs/path.db
    _, _, raw_path = database_url.partition(":///")
    if not raw_path:
        return None
    if raw_path == ":memory:":
        return None
    return Path(raw_path)


def rotate_backups(backup_dir: Path, keep_last: int = 7) -> list[Path]:
    """Removes old backup archives so only the `keep_last` most recent remain."""
    if keep_last < 1 or not backup_dir.exists():
        return []

    archives = sorted(
        backup_dir.glob("cliphunter_backup_*"),
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )
    removed: list[Path] = []
    for old_file in archives[keep_last:]:
        try:
            old_file.unlink()
            removed.append(old_file)
            log.info("Removed old database backup: %s", old_file.name)
        except OSError as exc:
            log.warning("Could not remove old backup %s: %s", old_file, exc)
    return removed


def backup_database(
    settings: Settings | None = None,
    out_dir: Path | None = None,
    keep_last: int = 7,
) -> Path:
    """Creates a compressed, timestamped backup of the database and rotates older snapshots."""
    app_settings = settings or get_settings()
    backup_dir = out_dir or (Path(app_settings.data_dir) / "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    sqlite_path = resolve_sqlite_path(app_settings.database_url)

    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure database file exists even if no job has been created yet
        if not sqlite_path.exists():
            conn = sqlite3.connect(str(sqlite_path))
            conn.close()

        archive_path = backup_dir / f"cliphunter_backup_{ts}.sqlite3.gz"
        # Ensure unique filename if called multiple times within the same second
        counter = 1
        while archive_path.exists():
            archive_path = backup_dir / f"cliphunter_backup_{ts}_{counter}.sqlite3.gz"
            counter += 1

        tmp_snapshot = backup_dir / f".tmp_snapshot_{ts}.sqlite3"
        try:
            src_conn = sqlite3.connect(str(sqlite_path))
            dst_conn = sqlite3.connect(str(tmp_snapshot))
            with dst_conn:
                src_conn.backup(dst_conn)
            dst_conn.close()
            src_conn.close()

            with tmp_snapshot.open("rb") as f_in, gzip.open(archive_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        finally:
            if tmp_snapshot.exists():
                tmp_snapshot.unlink()

        log.info(
            "Created SQLite backup %s (%d bytes)",
            archive_path.name,
            archive_path.stat().st_size,
        )
        rotate_backups(backup_dir, keep_last=keep_last)
        return archive_path

    # PostgreSQL backup via pg_dump
    archive_path = backup_dir / f"cliphunter_backup_{ts}.sql.gz"
    parsed = urlparse(app_settings.database_url.replace("+asyncpg", "").replace("+psycopg", ""))
    pg_url = parsed.geturl()
    proc = subprocess.run(
        ["pg_dump", pg_url],
        capture_output=True,
        check=True,
        timeout=300,
    )
    with gzip.open(archive_path, "wb") as f_out:
        f_out.write(proc.stdout)

    log.info("Created PostgreSQL backup %s", archive_path.name)
    rotate_backups(backup_dir, keep_last=keep_last)
    return archive_path


def restore_database(
    backup_path: Path,
    settings: Settings | None = None,
) -> Path:
    """Restores a SQLite database from a `.sqlite3.gz` or `.sqlite3` backup file."""
    if not backup_path.is_file():
        raise FileNotFoundError(f"Backup archive not found: {backup_path}")

    app_settings = settings or get_settings()
    sqlite_path = resolve_sqlite_path(app_settings.database_url)
    if sqlite_path is None:
        raise ValueError("Direct file restore is only supported for SQLite databases")

    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_restore = sqlite_path.with_suffix(".restore_tmp")

    try:
        if backup_path.suffix == ".gz":
            with gzip.open(backup_path, "rb") as f_in, tmp_restore.open("wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        else:
            shutil.copy2(backup_path, tmp_restore)

        # Verify SQLite integrity before overwriting active DB
        conn = sqlite3.connect(str(tmp_restore))
        try:
            res = conn.execute("PRAGMA integrity_check;").fetchone()
            if not res or res[0] != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {res}")
        finally:
            conn.close()

        if sqlite_path.exists():
            pre_bak = sqlite_path.with_suffix(".pre_restore.bak")
            shutil.copy2(sqlite_path, pre_bak)

        tmp_restore.replace(sqlite_path)
        log.info("Database restored from %s to %s", backup_path.name, sqlite_path)
        return sqlite_path
    finally:
        if tmp_restore.exists():
            tmp_restore.unlink()


def list_backups(
    out_dir: Path | None = None,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """Lists available database backup snapshots sorted newest-first."""
    app_settings = settings or get_settings()
    backup_dir = out_dir or (Path(app_settings.data_dir) / "backups")
    if not backup_dir.exists():
        return []

    items: list[dict[str, Any]] = []
    for p in sorted(
        backup_dir.glob("cliphunter_backup_*"),
        key=lambda f: (f.stat().st_mtime, f.name),
        reverse=True,
    ):
        stat = p.stat()
        items.append(
            {
                "name": p.name,
                "path": str(p),
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            }
        )
    return items
