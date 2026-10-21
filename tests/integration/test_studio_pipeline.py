"""Integration test for offline Studio JSON pipeline execution (0 USD, no LLM calls)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from cliphunter.config import Settings
from cliphunter.export.capcut import validate_capcut_draft
from cliphunter.llm.client import GeminiVideoClient
from cliphunter.models import JobParams
from cliphunter.pipeline.stages import PipelineRunner
from cliphunter.video.ffmpeg import run

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


class DummyFilesAPI:
    def upload(self, file: str) -> Any:
        raise AssertionError("Files API must not be called in Studio JSON mode!")


class DummyInteractionsAPI:
    def __init__(self) -> None:
        self.call_count = 0

    def create(self, **body: Any) -> Any:
        self.call_count += 1
        raise AssertionError("Interactions API must not be called in Studio JSON mode!")


class DummySDK:
    def __init__(self) -> None:
        self.files = DummyFilesAPI()
        self.interactions = DummyInteractionsAPI()


def create_synthetic_video(dst: Path, duration_s: int = 40) -> None:
    run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=640x360:rate=24:duration={duration_s}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={duration_s}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(dst),
        ],
        timeout_s=30,
    )


def test_studio_pipeline_offline_execution(tmp_path: Path) -> None:
    src_video = tmp_path / "raw_stream.mp4"
    create_synthetic_video(src_video, duration_s=40)

    studio_json = json.dumps(
        {
            "clips": [
                {
                    "start": "00:05",
                    "end": "00:25",
                    "working_title": "Free Studio Moment",
                    "category": "funny",
                    "score": 9,
                }
            ]
        }
    )

    sdk = DummySDK()
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        gemini_api_key="mock_key",
        capcut_export=True,
    )
    client = GeminiVideoClient(settings, sdk=sdk)
    runner = PipelineRunner(settings=settings, client=client)

    params = JobParams(
        auto_yes=True,
        capcut_export=True,
        studio_json=studio_json,
    )

    job = runner.run(target=str(src_video), params=params)

    # 1. Job completed with 0 USD cost and 0 LLM calls
    assert job.status == "completed"
    assert job.cost_usd == 0.0
    assert sdk.interactions.call_count == 0

    # 2. Rendered clip exists
    assert len(job.rendered_clips) == 1
    clip_file = Path(job.rendered_clips[0]["output_file"])
    assert clip_file.is_file()

    # 3. CapCut draft was exported and is valid
    draft_dir = Path(job.rendered_clips[0]["capcut_draft_dir"])
    assert draft_dir.is_dir()
    validation = validate_capcut_draft(draft_dir)
    assert validation["valid"] is True
