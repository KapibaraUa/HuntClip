"""Integration tests for Phase 7 system maintenance API endpoints and CLI commands."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from cliphunter.api.app import app
from cliphunter.cli import main as cli_main
from cliphunter.config import get_settings
from cliphunter.db.session import init_db


@pytest.fixture
def setup_test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "data" / "test_sys.db"
    test_db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()

    import cliphunter.db.session as session_module

    session_module._global_db = None
    return tmp_path


@pytest.fixture
async def async_client(setup_test_env: Path) -> AsyncGenerator[AsyncClient, None]:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_system_storage_backup_and_cleanup_api(async_client: AsyncClient) -> None:
    # 1. Storage status
    res = await async_client.get("/api/system/storage")
    assert res.status_code == 200
    data = res.json()
    assert "breakdown" in data
    assert "backups_count" in data

    # 2. Trigger DB backup
    bak_res = await async_client.post("/api/system/backup", json={"keep_last": 3})
    assert bak_res.status_code == 200
    bak_data = bak_res.json()
    assert bak_data["name"].endswith(".sqlite3.gz")
    assert bak_data["size_bytes"] > 0

    # 3. Trigger retention cleanup (dry run)
    clean_res = await async_client.post(
        "/api/system/cleanup",
        json={"retention_days": 14, "dry_run": True},
    )
    assert clean_res.status_code == 200
    clean_data = clean_res.json()
    assert clean_data["dry_run"] is True
    assert clean_data["retention_days"] == 14


def test_cli_backup_and_cleanup_commands(setup_test_env: Path) -> None:
    # Create a backup via CLI
    rc_bak = cli_main(["backup", "--keep", "2"])
    assert rc_bak == 0

    # List backups via CLI
    rc_list = cli_main(["backup", "--list"])
    assert rc_list == 0

    # Run cleanup dry-run via CLI
    rc_clean = cli_main(["cleanup", "--days", "7", "--dry-run"])
    assert rc_clean == 0
