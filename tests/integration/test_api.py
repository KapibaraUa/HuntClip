"""Integration tests for ClipHunter FastAPI endpoints and Web Review flow."""

from __future__ import annotations

import io
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from cliphunter.api.app import app
from cliphunter.db.models import ClipORM, SourceORM
from cliphunter.db.repo import save_clip, update_job_progress
from cliphunter.db.session import get_session_factory, init_db


@pytest.fixture
def setup_test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Configures isolated SQLite database for integration tests."""
    db_path = tmp_path / "test_api.db"
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
async def test_root_and_openapi(async_client: AsyncClient) -> None:
    """Verifies index page and OpenAPI schema are accessible."""
    res = await async_client.get("/")
    assert res.status_code == 200
    assert "ClipHunter" in res.text

    openapi_res = await async_client.get("/openapi.json")
    assert openapi_res.status_code == 200
    schema = openapi_res.json()
    assert schema["info"]["title"] == "ClipHunter"


@pytest.mark.asyncio
async def test_profiles_api(async_client: AsyncClient) -> None:
    """Tests GET and PUT profiles."""
    res = await async_client.get("/api/profiles")
    assert res.status_code == 200
    profiles = res.json()
    assert len(profiles) >= 1
    assert any(p["name"] == "default" for p in profiles)

    # Get single profile
    res_one = await async_client.get("/api/profiles/evelone2004")
    assert res_one.status_code == 200
    data = res_one.json()
    assert data["name"] == "evelone2004"
    assert data["game"] == "CS2 / Just Chatting"

    # Create/update custom profile
    payload = {
        "name": "custom_streamer",
        "language": "ru",
        "game": "Dota 2",
        "clip_min_s": 15.0,
        "clip_max_s": 45.0,
        "prompt_addon": "Фокус на переворотах в тимфайтах.",
    }
    put_res = await async_client.put("/api/profiles/custom_streamer", json=payload)
    assert put_res.status_code == 200
    assert put_res.json()["name"] == "custom_streamer"


@pytest.mark.asyncio
async def test_file_upload_api(async_client: AsyncClient) -> None:
    """Tests multipart file upload route."""
    file_content = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41"
    files = {"file": ("stream_sample.mp4", io.BytesIO(file_content), "video/mp4")}

    res = await async_client.post("/api/uploads", files=files)
    assert res.status_code == 200
    data = res.json()
    assert "source_id" in data
    assert data["filename"] == "stream_sample.mp4"
    assert "sha256" in data


@pytest.mark.asyncio
async def test_job_lifecycle_and_review_flow(async_client: AsyncClient, tmp_path: Path) -> None:
    """Full lifecycle: create job, add manual candidate, fine-tune ±0.1s, render, download."""
    # 1. Create a dummy video file so probe and proxy work
    test_video = tmp_path / "stream_mock.mp4"
    test_video.write_bytes(b"dummy mp4 content for testing range requests and boundaries")

    # Seed source in DB
    factory = get_session_factory()
    async with factory() as session:
        src = SourceORM(
            id="src_test_1",
            kind="file",
            path=str(test_video),
            duration_s=600.0,
            title="Test Stream VOD",
            channel="TestChannel",
            status="ready",
        )
        session.add(src)
        await session.commit()

    # 2. Create Job via POST /api/jobs
    create_payload = {
        "source_id": "src_test_1",
        "profile_id": "evelone2004",
        "clip_min_s": 20.0,
        "clip_max_s": 60.0,
        "max_cost_usd": 3.0,
    }
    job_res = await async_client.post("/api/jobs", json=create_payload)
    assert job_res.status_code == 200
    job_data = job_res.json()
    job_id = job_data["id"]
    assert job_data["profile_id"] == "evelone2004"
    assert job_data["source_title"] == "Test Stream VOD"

    # 3. Add manual candidate moment (POST /api/jobs/{id}/candidates)
    cand_payload = {
        "start_s": 120.0,
        "end_s": 155.5,
        "working_title": "Epic clutch reaction",
        "category": "game_moment",
        "score": 9,
        "why_viral": "Streamer screams in joy",
    }
    cand_res = await async_client.post(f"/api/jobs/{job_id}/candidates", json=cand_payload)
    assert cand_res.status_code == 200
    cand_data = cand_res.json()
    cand_id = cand_data["id"]
    assert cand_data["working_title"] == "Epic clutch reaction"
    assert cand_data["status"] == "accepted"
    assert cand_data["score"] == 9

    # 4. Fine-tune candidate boundaries by ±0.1s (PATCH /api/candidates/{id})
    patch_payload = {
        "start_s": 119.9,  # nudged by -0.1s
        "end_s": 156.0,  # nudged by +0.5s
        "status": "accepted",
    }
    patch_res = await async_client.patch(f"/api/candidates/{cand_id}", json=patch_payload)
    assert patch_res.status_code == 200
    patched_data = patch_res.json()
    assert patched_data["start_s"] == 119.9
    assert patched_data["end_s"] == 156.0
    assert patched_data["status"] == "accepted"

    # 5. List candidates (GET /api/jobs/{id}/candidates)
    list_cand_res = await async_client.get(f"/api/jobs/{job_id}/candidates")
    assert list_cand_res.status_code == 200
    candidates = list_cand_res.json()
    assert len(candidates) == 1
    assert candidates[0]["id"] == cand_id

    # 6. Verify HTTP 206 Partial Content Media Streaming
    # Set proxy_path on job
    async with factory() as session:
        await update_job_progress(session, job_id, proxy_path=str(test_video))
        await session.commit()

    # Request range bytes=0-10
    media_res = await async_client.get(
        f"/api/media/proxy/{job_id}", headers={"Range": "bytes=0-10"}
    )
    assert media_res.status_code == 206
    assert media_res.headers["Content-Range"].startswith("bytes 0-10/")
    assert len(media_res.content) == 11

    # 7. Render dummy clip and verify download endpoint
    clip_file = tmp_path / "clip_rendered.mp4"
    clip_file.write_bytes(b"rendered mp4 binary stream")

    async with factory() as session:
        clip_orm = ClipORM(
            id=f"clip_{cand_id}",
            job_id=job_id,
            candidate_id=cand_id,
            path=str(clip_file),
            layout="original",
            template="default",
            duration_s=36.1,
            manifest={"title": "Epic clutch reaction", "score": 9},
        )
        await save_clip(session, clip_orm)
        await session.commit()

    # List clips
    clips_res = await async_client.get(f"/api/jobs/{job_id}/clips")
    assert clips_res.status_code == 200
    clips_list = clips_res.json()
    assert len(clips_list) == 1
    assert clips_list[0]["id"] == f"clip_{cand_id}"

    # Download clip
    dl_res = await async_client.get(f"/api/clips/clip_{cand_id}/download")
    assert dl_res.status_code == 200
    assert dl_res.headers["content-type"] == "video/mp4"
    assert dl_res.content == b"rendered mp4 binary stream"

    # 8. Check Usage endpoint (GET /api/jobs/{id}/usage)
    usage_res = await async_client.get(f"/api/jobs/{job_id}/usage")
    assert usage_res.status_code == 200
    usage_data = usage_res.json()
    assert usage_data["job_id"] == job_id
    assert "total_tokens" in usage_data
    assert "total_cost_usd" in usage_data
