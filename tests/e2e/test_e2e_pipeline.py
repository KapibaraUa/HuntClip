"""End-to-end (E2E) tests for ClipHunter pipeline, budget pause/resume, settings screen, and RUN_LIVE=1 mode (plan, Section 13)."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from cliphunter.api.app import app
from cliphunter.config import Settings, get_settings
from cliphunter.db.models import CandidateORM, JobORM, SourceORM
from cliphunter.db.session import get_db_manager, init_db
from cliphunter.export.capcut import validate_capcut_draft
from cliphunter.llm.budget import Budget
from cliphunter.llm.client import GeminiVideoClient
from cliphunter.models import JobParams
from cliphunter.pipeline.stages import PipelineRunner
from cliphunter.video.audio import build_afade_filter, build_audio_filter, build_loudnorm_filter
from cliphunter.video.cut import verify_rendered_clip


def _generate_synthetic_video(dst: Path, duration_s: float = 35.0) -> Path:
    """Generates a valid H.264 + AAC MP4 test video using FFmpeg lavfi sources."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=1280x720:rate=24:duration={duration_s}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=44100:duration={duration_s}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        str(dst),
    ]
    subprocess.run(cmd, check=True, timeout=60)
    return dst


class _MockFilesAPI:
    def upload(self, file: str) -> Any:
        return SimpleNamespace(
            name="files/e2e_test_id", uri="files/e2e_test_id", mime_type="video/mp4", state="ACTIVE"
        )

    def get(self, name: str) -> Any:
        return SimpleNamespace(name=name, uri=name, mime_type="video/mp4", state="ACTIVE")


class _MockInteractionsAPI:
    def __init__(self, clips: list[dict[str, Any]]) -> None:
        self.clips = clips
        self.call_count = 0

    def create(self, **body: Any) -> Any:
        self.call_count += 1
        fmt = body.get("response_format", {})
        schema = fmt.get("schema", {})
        title = schema.get("title", "")
        if title == "RefineResponse" or "verdict" in schema.get("properties", {}):
            resp = json.dumps(
                {
                    "verdict": "keep",
                    "start": "00:05",
                    "end": "00:27",
                    "score": 9,
                    "cut_inside": [],
                    "reason": "Отличные границы момента",
                }
            )
        elif title == "JudgeResponse" or "items" in schema.get("properties", {}):
            input_parts = body.get("input", [])
            video_count = max(
                1,
                sum(1 for p in input_parts if isinstance(p, dict) and p.get("type") == "video"),
            )
            items = [
                {
                    "clip_index": i,
                    "verdict": "keep",
                    "score": 9,
                    "hook": 9,
                    "emotion": 9,
                    "completeness": 9,
                    "standalone": 8,
                    "reason": "Сильный хук и концовка",
                    "title_options": ["Клатч года!", "Невероятный финал"],
                }
                for i in range(1, video_count + 1)
            ]
            resp = json.dumps({"items": items})
        else:
            resp = json.dumps({"notes": "E2E scan pass", "clips": self.clips})

        usage = SimpleNamespace(
            total_input_tokens=8000,
            total_output_tokens=250,
            total_thought_tokens=400,
            total_tool_use_tokens=0,
        )
        return SimpleNamespace(output_text=resp, usage=usage)


class _MockGenAISDK:
    def __init__(self, clips: list[dict[str, Any]]) -> None:
        self.files = _MockFilesAPI()
        self.interactions = _MockInteractionsAPI(clips)


def test_audio_module_filters() -> None:
    assert build_loudnorm_filter() == "loudnorm=I=-16:TP=-1.5:LRA=11"
    assert "afade=t=in:st=0:d=0.1" in build_afade_filter(25.0)
    combined = build_audio_filter(25.0, normalize_loudness=True)
    assert combined.startswith("loudnorm=I=-16:TP=-1.5:LRA=11,afade=")


def test_e2e_full_pipeline_with_capcut_and_manifest(tmp_path: Path) -> None:
    """Runs S0 -> S9 end-to-end on a synthetic video, verifying MP4, manifest.json, and CapCut draft."""
    video_path = _generate_synthetic_video(tmp_path / "input_stream.mp4", duration_s=35.0)
    mock_clips = [
        {
            "start": "00:05",
            "end": "00:28",
            "confidence_timestamps": "high",
            "category": "game_moment",
            "working_title": "E2E Клатч в финале",
            "hook_text": "Смотри до конца!",
            "visual_hook": "Взрыв на точке",
            "what_happens": "Стример выигрывает раунд 1 в 3",
            "visual_evidence": "Экран победы",
            "audio_evidence": "Крик радости",
            "why_viral": "Высокая динамика и эмоция",
            "ending_text": "Победа!",
            "cuttable_inside": [],
            "title_options": ["Клатч 1 в 3", "Невероятный финал"],
            "score": 9,
            "risks": [],
        }
    ]
    sdk = _MockGenAISDK(mock_clips)
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        gemini_api_key="mock_key",
        clip_min_s=15.0,
        clip_max_s=30.0,
        capcut_export=True,
    )
    client = GeminiVideoClient(settings, budget=Budget(limit_usd=5.0), sdk=sdk)
    runner = PipelineRunner(settings=settings, client=client)

    params = JobParams(
        clip_min_s=15.0,
        clip_max_s=30.0,
        max_cost_usd=2.0,
        auto_yes=True,
        capcut_export=True,
    )
    job = runner.run(target=str(video_path), params=params)

    assert job.status == "completed"
    assert len(job.rendered_clips) == 1
    assert 0 < job.cost_usd <= 2.0

    clip_path = Path(job.rendered_clips[0]["output_file"])
    assert clip_path.is_file()
    probe_report = verify_rendered_clip(clip_path, expected_dur_s=22.0, tolerance_s=2.0)
    assert probe_report["has_video"] is True
    assert probe_report["has_audio"] is True
    assert probe_report["actual_duration_s"] > 20.0

    out_dir = Path(job.output_dir or "")
    manifest_file = out_dir / "manifest.json"
    assert manifest_file.is_file()
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest_data["job_id"] == job.id
    assert len(manifest_data["clips"]) == 1

    # Verify CapCut draft folder was exported and is valid
    draft_dir = Path(manifest_data["clips"][0]["capcut_draft_dir"])
    assert draft_dir.is_dir()
    validation = validate_capcut_draft(draft_dir)
    assert validation["valid"] is True


@pytest.fixture
def setup_e2e_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "data" / "e2e.db"
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
async def e2e_client(setup_e2e_db: Path) -> AsyncGenerator[AsyncClient, None]:
    await init_db()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_e2e_settings_budget_resume_and_usage_funnel(e2e_client: AsyncClient) -> None:
    # 1. GET & PUT /api/system/settings (Screen 5 Settings)
    get_res = await e2e_client.get("/api/system/settings")
    assert get_res.status_code == 200
    assert get_res.json()["gemini_model"] == "gemini-3.8-flash"

    put_res = await e2e_client.put(
        "/api/system/settings",
        json={
            "thinking_level": "high",
            "max_cost_usd_per_job": 8.5,
            "auto_render": True,
        },
    )
    assert put_res.status_code == 200
    updated = put_res.json()
    assert updated["thinking_level"] == "high"
    assert updated["max_cost_usd_per_job"] == 8.5
    assert updated["auto_render"] is True

    # 2. Seed a paused_budget job with candidates and test budget increase + usage funnel
    db = get_db_manager()
    async with db.session_factory() as session:
        session.add(
            SourceORM(
                id="src_e2e_1",
                kind="file",
                path="dummy.mp4",
                duration_s=600.0,
                title="E2E Stream",
                channel="Streamer",
            )
        )
        session.add(
            JobORM(
                id="job_paused_1",
                source_id="src_e2e_1",
                profile_id="default",
                params={"max_cost_usd": 1.0},
                stage="s3_scan",
                status="paused_budget",
                cost_usd=0.95,
                budget_usd=1.0,
                error="Budget exceeded",
            )
        )
        session.add(
            CandidateORM(
                id="cand_e2e_1",
                job_id="job_paused_1",
                chunk_id="chunk_0",
                start_s=10.0,
                end_s=35.0,
                category="funny",
                working_title="Funny moment",
                score=8,
                status="accepted",
                source_pass="judge",
                prompt_version=1,
                data={},
            )
        )
        await session.commit()

    # Check usage funnel metrics (Section 12)
    usage_res = await e2e_client.get("/api/jobs/job_paused_1/usage")
    assert usage_res.status_code == 200
    usage = usage_res.json()
    assert "funnel" in usage
    assert usage["funnel"]["total"] == 1
    assert usage["funnel"]["accepted"] == 1

    # Raise budget and resume job (Section 8)
    with patch("cliphunter.api.routes_jobs.task_manager.start_job") as mock_start:
        budget_res = await e2e_client.post(
            "/api/jobs/job_paused_1/budget",
            json={"max_cost_usd": 5.0, "resume": True},
        )
        assert budget_res.status_code == 200
        b_data = budget_res.json()
        assert b_data["budget_usd"] == 5.0
        assert b_data["status"] == "running"
        assert b_data["resumed"] is True
        mock_start.assert_called_once_with("job_paused_1")


@pytest.mark.skipif(
    os.getenv("RUN_LIVE") != "1" or not os.getenv("GEMINI_API_KEY"),
    reason="Live Gemini E2E test runs only when RUN_LIVE=1 and GEMINI_API_KEY are set",
)
def test_e2e_live_gemini_api(tmp_path: Path) -> None:
    """Section 13: Real API call on a short video when RUN_LIVE=1 is enabled."""
    video_path = _generate_synthetic_video(tmp_path / "live_test.mp4", duration_s=45.0)
    settings = Settings(
        data_dir=tmp_path / "data",
        clip_min_s=10.0,
        clip_max_s=35.0,
        max_cost_usd_per_job=0.50,
    )
    runner = PipelineRunner(settings=settings)
    params = JobParams(
        clip_min_s=10.0,
        clip_max_s=35.0,
        max_cost_usd=0.50,
        auto_yes=True,
    )
    job = runner.run(target=str(video_path), params=params)
    assert job.cost_usd <= 0.50
