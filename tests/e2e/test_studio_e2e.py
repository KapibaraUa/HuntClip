"""E2E API test for Google AI Studio JSON import."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from cliphunter.api.app import app
from cliphunter.config import get_settings
from cliphunter.db.models import SourceORM
from cliphunter.db.session import get_db_manager, init_db


@pytest.fixture
def setup_studio_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "data" / "studio_e2e.db"
    test_db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()

    import cliphunter.db.session as session_module
    from cliphunter.worker.tasks import task_manager

    session_module._global_db = None
    task_manager.db = session_module.get_db_manager()
    return tmp_path


@pytest.fixture
async def studio_client(setup_studio_db: Path) -> AsyncGenerator[AsyncClient, None]:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_parse_studio_json_endpoint(studio_client: AsyncClient) -> None:
    raw_markdown = """
    ```json
    {
      "clips": [
        {
          "start": "00:10",
          "end": "00:40",
          "title": "Студийный хайлайт",
          "category": "gameplay",
          "score": 9,
          "why_viral": "Крутой момент"
        }
      ]
    }
    ```
    """
    res = await studio_client.post("/api/jobs/parse-studio-json", json={"raw_text": raw_markdown})
    assert res.status_code == 200
    data = res.json()
    assert data["valid"] is True
    assert data["count"] == 1
    cand = data["candidates"][0]
    assert cand["start_s"] == 10.0
    assert cand["end_s"] == 40.0
    assert cand["title"] == "Студийный хайлайт"
    assert cand["category"] == "game_moment"


@pytest.mark.asyncio
async def test_create_job_with_studio_json(
    studio_client: AsyncClient, setup_studio_db: Path
) -> None:
    # 1. Create a dummy source
    db = get_db_manager()
    dummy_video = setup_studio_db / "data" / "sample.mp4"
    dummy_video.parent.mkdir(parents=True, exist_ok=True)
    dummy_video.write_bytes(b"dummy")

    async with db.session_factory() as session:
        session.add(
            SourceORM(
                id="src_studio_test",
                kind="file",
                path=str(dummy_video),
                duration_s=120.0,
                title="Test Studio VOD",
                channel="Streamer",
            )
        )
        await session.commit()

    studio_payload = json.dumps(
        {
            "clips": [
                {
                    "start": "00:15",
                    "end": "00:45",
                    "title": "Хайлайт из студии",
                    "category": "funny",
                }
            ]
        }
    )

    create_res = await studio_client.post(
        "/api/jobs",
        json={
            "source_id": "src_studio_test",
            "profile_id": "default",
            "studio_json": studio_payload,
        },
    )
    assert create_res.status_code == 200
    job_data = create_res.json()
    assert job_data["cost_usd"] == 0.0
