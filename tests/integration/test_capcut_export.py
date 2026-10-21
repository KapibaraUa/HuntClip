"""Integration tests for Phase 5 CapCut draft export (FFmpeg + FastAPI + CLI)."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from cliphunter.api.app import app
from cliphunter.cli import main as cli_main
from cliphunter.db.models import ClipORM, JobORM, SourceORM
from cliphunter.db.repo import save_clip
from cliphunter.db.session import get_session_factory, init_db
from cliphunter.export.capcut import CapCutClipSpec, CapCutExporter, validate_capcut_draft
from cliphunter.models import Candidate, CandidateStatus
from cliphunter.video.ffmpeg import run
from cliphunter.video.render import render_candidate


@pytest.fixture
def synthetic_video(tmp_path: Path) -> Path:
    """Generates a 3-second 1920x1080 synthetic video with audio via FFmpeg."""
    out = tmp_path / "source_1080p.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=3:size=1920x1080:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=3",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(out),
    ]
    run(cmd, timeout_s=30)
    return out


def test_real_rendered_clip_capcut_export(synthetic_video: Path, tmp_path: Path) -> None:
    """Renders a vertical 9:16 clip with FFmpeg and exports a verified CapCut draft."""
    cand = Candidate(
        id="cand_cc_1",
        start_s=0.5,
        end_s=2.5,
        category="game_moment",
        working_title="Клатч 1 в 4 на последних секундах",
        score=9,
        status=CandidateStatus.ACCEPTED,
    )

    out_dir = tmp_path / "out"
    clip_mp4 = render_candidate(
        candidate=cand,
        src=synthetic_video,
        out_dir=out_dir,
        idx=1,
        layout="blur_fit_9x16",
        title=cand.working_title,
    )

    exporter = CapCutExporter(out_dir=out_dir)
    draft_dir = exporter.export(
        CapCutClipSpec(
            clip_id="clip_01_blur_fit",
            video_path=clip_mp4,
            title=cand.working_title,
            layout="blur_fit_9x16",
        )
    )

    info = validate_capcut_draft(draft_dir)
    assert info["valid"] is True
    assert info["width"] == 1080
    assert info["height"] == 1920
    assert info["ratio"] == "9:16"
    assert abs(info["duration_us"] - 2_000_000) <= 150_000

    # Test CLI capcut-export on the rendered MP4
    rc = cli_main(
        [
            "capcut-export",
            str(clip_mp4),
            "--title",
            "CLI Тест Заголовка",
            "--layout",
            "blur_fit_9x16",
            "--out",
            str(tmp_path / "cli_out"),
        ]
    )
    assert rc == 0
    cli_draft = tmp_path / "cli_out" / "capcut_drafts" / clip_mp4.stem
    assert validate_capcut_draft(cli_draft)["valid"] is True


@pytest.fixture
def setup_api_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "test_cc_api.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    import cliphunter.db.session as session_module
    from cliphunter.worker.tasks import task_manager

    session_module._global_db = None
    task_manager.db = session_module.get_db_manager()
    return tmp_path


@pytest.fixture
async def async_client(setup_api_db: Path) -> AsyncGenerator[AsyncClient, None]:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_api_capcut_draft_export_and_zip_download(
    async_client: AsyncClient, tmp_path: Path
) -> None:
    """Tests POST /api/clips/{id}/capcut and GET /api/clips/{id}/capcut/download."""
    clip_file = tmp_path / "api_clip.mp4"
    clip_file.write_bytes(b"rendered mp4 binary content")

    factory = get_session_factory()
    async with factory() as session:
        src = SourceORM(
            id="src_cc",
            kind="file",
            path=str(clip_file),
            duration_s=120.0,
            title="Stream",
            channel="Streamer",
            status="ready",
        )
        job = JobORM(
            id="job_cc_1",
            source_id="src_cc",
            profile_id="default",
            stage="done",
            status="completed",
            output_dir=str(tmp_path / "job_out"),
        )
        clip = ClipORM(
            id="clip_cc_01",
            job_id="job_cc_1",
            candidate_id="cand_01",
            path=str(clip_file),
            layout="center_crop_9x16",
            template="default",
            duration_s=25.0,
            manifest={"title": "Топовый игровой момент", "score": 9},
        )
        session.add(src)
        session.add(job)
        await save_clip(session, clip)
        await session.commit()

    # 1. Trigger CapCut export via POST
    post_res = await async_client.post("/api/clips/clip_cc_01/capcut")
    assert post_res.status_code == 200
    data = post_res.json()
    assert data["clip_id"] == "clip_cc_01"
    assert data["validation"]["valid"] is True

    # 2. Download .zip via GET
    zip_res = await async_client.get("/api/clips/clip_cc_01/capcut/download")
    assert zip_res.status_code == 200
    assert zip_res.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(zip_res.content), "r") as zf:
        content_raw = zf.read("clip_cc_01/draft_content.json").decode("utf-8")
        content_json = json.loads(content_raw)
        assert content_json["duration"] == 25_000_000
        text_mat = content_json["materials"]["texts"][0]
        assert json.loads(text_mat["content"])["text"] == "Топовый игровой момент"
