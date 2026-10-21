"""Integration tests for batch jobs queue, profile editor API, and Twitch sources (Phase 6)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from cliphunter.api.app import app
from cliphunter.db.session import init_db
from cliphunter.ingest.probe import is_twitch_url, is_youtube_url, probe_source
from cliphunter.models import Source, SourceKind


@pytest.fixture
def setup_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Configures isolated SQLite database for integration tests."""
    db_path = tmp_path / "test_batch.db"
    test_db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    import cliphunter.db.session as session_module
    from cliphunter.worker.tasks import task_manager

    session_module._global_db = None
    task_manager.db = session_module.get_db_manager()
    return tmp_path


@pytest.fixture
async def async_client(setup_test_db: Path) -> AsyncGenerator[AsyncClient, None]:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_batch_create_jobs_endpoint(async_client: AsyncClient) -> None:
    fake_source = Source(
        id="src_dummy",
        kind=SourceKind.FILE,
        path="dummy.mp4",
        duration_s=120.0,
        title="Test Batch Source",
    )

    with (
        patch("cliphunter.api.routes_jobs.probe_source", return_value=fake_source),
        patch("cliphunter.worker.tasks.task_manager.start_job") as mock_start,
    ):
        res = await async_client.post(
            "/api/jobs/batch",
            json={
                "urls": ["https://youtube.com/watch?v=batch1", "https://twitch.tv/videos/123456"],
                "profile_id": "default",
                "auto_yes": True,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert "batch_id" in data
        assert data["total"] == 2
        assert data["created_count"] == 2
        assert len(data["jobs"]) == 2
        assert mock_start.call_count == 2


@pytest.mark.asyncio
async def test_batch_empty_urls_rejected(async_client: AsyncClient) -> None:
    res = await async_client.post("/api/jobs/batch", json={"urls": []})
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_profile_crud_api(async_client: AsyncClient) -> None:
    # 1. List initial profiles
    res = await async_client.get("/api/profiles")
    assert res.status_code == 200
    profiles = res.json()
    assert len(profiles) >= 1
    assert any(p["name"] == "default" for p in profiles)

    # 2. Create custom streamer profile
    new_prof = {
        "name": "custom_streamer",
        "language": "ru",
        "game": "Dota 2",
        "clip_min_s": 15.0,
        "clip_max_s": 60.0,
        "prompt_addon": "Ищи рампейджи и смешные рейдж-моменты.",
        "facecam_rect": {"x": 0.75, "y": 0.05, "w": 0.22, "h": 0.25},
        "gameplay_rect": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
    }
    put_res = await async_client.put("/api/profiles/custom_streamer", json=new_prof)
    assert put_res.status_code == 200
    saved = put_res.json()
    assert saved["name"] == "custom_streamer"
    assert saved["game"] == "Dota 2"
    assert saved["facecam_rect"]["w"] == 0.22

    # 3. Get profile by name
    get_res = await async_client.get("/api/profiles/custom_streamer")
    assert get_res.status_code == 200
    assert get_res.json()["prompt_addon"] == "Ищи рампейджи и смешные рейдж-моменты."


def test_probe_source_twitch_detection() -> None:
    assert is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True
    assert is_twitch_url("https://www.twitch.tv/evelone2004") is True
    assert is_twitch_url("https://clips.twitch.tv/FamousStreamerClip") is True

    # Test probing a twitch url with mocked fetch_info
    fake_info = {
        "id": "12345678",
        "title": "Twitch Championship",
        "uploader": "esl_csgo",
        "duration": 3600.0,
        "is_live": False,
    }
    with patch("cliphunter.ingest.probe.fetch_info", return_value=fake_info):
        src = probe_source("https://www.twitch.tv/videos/12345678")
        assert src.id == "twitch_12345678"
        assert src.kind == SourceKind.OTHER_URL
        assert src.duration_s == 3600.0
        assert src.title == "Twitch Championship"
