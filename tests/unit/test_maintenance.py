"""Unit tests for Phase 7 maintenance utilities: DB backup/restore, retention cleanup, log rotation, and env check."""

from __future__ import annotations

import gzip
import os
import sqlite3
import time
from pathlib import Path

from cliphunter.config import Settings
from cliphunter.maintenance.backup import (
    backup_database,
    list_backups,
    resolve_sqlite_path,
    restore_database,
)
from cliphunter.maintenance.cleanup import (
    cleanup_expired_data,
    get_storage_stats,
    rotate_logs,
)


def test_resolve_sqlite_path() -> None:
    assert resolve_sqlite_path("sqlite+aiosqlite:///data/cliphunter.db") == Path(
        "data/cliphunter.db"
    )
    assert resolve_sqlite_path("sqlite:///:memory:") is None
    assert resolve_sqlite_path("postgresql+psycopg://user:pass@localhost/db") is None


def test_backup_and_restore_sqlite_roundtrip(tmp_path: Path) -> None:
    db_file = tmp_path / "data" / "cliphunter.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)

    # Create sample SQLite DB with data
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT);")
    conn.execute("INSERT INTO items (name) VALUES ('viral_moment_1'), ('viral_moment_2');")
    conn.commit()
    conn.close()

    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite+aiosqlite:///{db_file.as_posix()}",
    )

    archive_path = backup_database(settings=settings, keep_last=3)
    assert archive_path.is_file()
    assert archive_path.name.endswith(".sqlite3.gz")

    backups = list_backups(settings=settings)
    assert len(backups) == 1
    assert backups[0]["name"] == archive_path.name

    # Mutate active DB (delete rows)
    conn = sqlite3.connect(str(db_file))
    conn.execute("DELETE FROM items;")
    conn.commit()
    conn.close()

    # Restore from backup
    restored_path = restore_database(archive_path, settings=settings)
    assert restored_path == db_file
    assert db_file.with_suffix(".pre_restore.bak").is_file()

    # Verify rows are back
    conn = sqlite3.connect(str(db_file))
    rows = conn.execute("SELECT name FROM items ORDER BY id;").fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["viral_moment_1", "viral_moment_2"]


def test_backup_rotation_keeps_only_recent(tmp_path: Path) -> None:
    db_file = tmp_path / "data" / "cliphunter.db"
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite+aiosqlite:///{db_file.as_posix()}",
    )

    for _ in range(4):
        backup_database(settings=settings, keep_last=2)

    backups = list_backups(settings=settings)
    assert len(backups) == 2


def test_cleanup_expired_data_preserves_state_and_rendered_clips(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    sources_dir = data_dir / "sources"
    work_dir = data_dir / "jobs" / "job_abc" / "work"
    out_dir = data_dir / "jobs" / "job_abc" / "out"
    cache_dir = data_dir / "cache" / "llm"

    for d in (sources_dir, work_dir, out_dir, cache_dir):
        d.mkdir(parents=True, exist_ok=True)

    old_source = sources_dir / "old_stream.mp4"
    fresh_source = sources_dir / "fresh_stream.mp4"
    old_proxy = work_dir / "proxy_720p.mp4"
    state_json = work_dir / "state.json"
    rendered_clip = out_dir / "01_clip.mp4"
    old_cache = cache_dir / "hash123.json"

    for f in (old_source, fresh_source, old_proxy, state_json, rendered_clip, old_cache):
        f.write_bytes(b"x" * 1024)

    # Set mtime of old files + state_json + rendered_clip to 20 days ago
    twenty_days_ago = time.time() - (20 * 86400)
    for f in (old_source, old_proxy, state_json, rendered_clip, old_cache):
        os.utime(f, (twenty_days_ago, twenty_days_ago))

    settings = Settings(data_dir=data_dir, retention_days=14)

    # 1. Dry run should not delete anything
    dry_report = cleanup_expired_data(settings=settings, dry_run=True)
    assert dry_report.deleted_files_count == 3  # old_source, old_proxy, old_cache
    assert old_source.exists()
    assert old_proxy.exists()
    assert old_cache.exists()

    # 2. Live run deletes expired temp/source/cache files, preserves state.json, out/, and fresh files
    live_report = cleanup_expired_data(settings=settings, dry_run=False)
    assert live_report.deleted_files_count == 3
    assert live_report.freed_bytes == 3 * 1024
    assert not old_source.exists()
    assert not old_proxy.exists()
    assert not old_cache.exists()

    # Crucial invariants: state.json, final rendered clips, and fresh sources remain untouched
    assert state_json.exists()
    assert rendered_clip.exists()
    assert fresh_source.exists()


def test_rotate_logs_compresses_and_truncates(tmp_path: Path) -> None:
    logs_dir = tmp_path / "data" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    log_file = logs_dir / "api_calls.jsonl"
    payload = b'{"model": "gemini-3.8-flash", "cost_usd": 0.01}\n' * 50
    log_file.write_bytes(payload)

    rotated = rotate_logs(logs_dir, max_size_bytes=100, keep_archives=2)
    assert len(rotated) == 1
    assert rotated[0].is_file()
    assert log_file.stat().st_size == 0

    with gzip.open(rotated[0], "rb") as f:
        decompressed = f.read()
    assert decompressed == payload


def test_get_storage_stats(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "sources").mkdir(parents=True, exist_ok=True)
    (data_dir / "sources" / "a.mp4").write_bytes(b"12345")

    settings = Settings(data_dir=data_dir)
    stats = get_storage_stats(settings=settings)
    assert stats["total_files"] == 1
    assert stats["total_bytes"] == 5
    assert stats["breakdown"]["sources"]["files"] == 1
