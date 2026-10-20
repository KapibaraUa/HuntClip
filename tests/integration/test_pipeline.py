import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cliphunter.config import Settings
from cliphunter.llm.budget import Budget
from cliphunter.llm.client import GeminiVideoClient
from cliphunter.models import JobParams, StageEnum
from cliphunter.pipeline.stages import PipelineRunner
from cliphunter.video.ffmpeg import run

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


class MockFilesAPI:
    def upload(self, file: str) -> Any:
        return SimpleNamespace(
            name="files/test_id", uri="files/test_id", mime_type="video/mp4", state="ACTIVE"
        )

    def get(self, name: str) -> Any:
        return SimpleNamespace(name=name, uri=name, mime_type="video/mp4", state="ACTIVE")


class MockInteractionsAPI:
    def __init__(self, clips: list[dict[str, Any]]) -> None:
        self.clips = clips
        self.call_count = 0

    def create(self, **body: Any) -> Any:
        self.call_count += 1
        resp = json.dumps({"notes": None, "clips": self.clips})
        usage = SimpleNamespace(
            total_input_tokens=10000,
            total_output_tokens=300,
            total_thought_tokens=500,
            total_tool_use_tokens=0,
        )
        return SimpleNamespace(output_text=resp, usage=usage)


class MockGenAISDK:
    def __init__(self, clips: list[dict[str, Any]]) -> None:
        self.files = MockFilesAPI()
        self.interactions = MockInteractionsAPI(clips)


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


def test_full_pipeline_run_and_idempotence(tmp_path: Path) -> None:
    src_video = tmp_path / "raw_stream.mp4"
    create_synthetic_video(src_video, duration_s=45)

    mock_clips = [
        {
            "start": "00:05",
            "end": "00:25",
            "confidence_timestamps": "high",
            "category": "funny",
            "working_title": "Funny Reaction",
            "visual_hook": "Face reaction",
            "what_happens": "Streamer laughs at funny moment",
            "visual_evidence": "Smiles and claps",
            "audio_evidence": "Laughing audio",
            "why_viral": "High emotion punchline",
            "title_options": ["Unstoppable Laugh", "Streamer Lost It"],
            "score": 9,
        }
    ]

    sdk = MockGenAISDK(mock_clips)
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        gemini_api_key="mock_key",
        clip_min_s=15.0,
        clip_max_s=35.0,
    )
    client = GeminiVideoClient(settings, budget=Budget(5.0), sdk=sdk)

    runner = PipelineRunner(settings=settings, client=client)
    params = JobParams(clip_min_s=15.0, clip_max_s=35.0, auto_yes=True)

    # 1. Run pipeline from scratch
    job = runner.run(target=str(src_video), params=params)

    assert job.status == "completed"
    assert job.stage == StageEnum.DONE
    assert len(job.rendered_clips) == 1
    assert job.cost_usd > 0.0
    assert sdk.interactions.call_count == 1

    out_clip = Path(job.rendered_clips[0]["output_file"])
    assert out_clip.exists()
    assert (Path(job.output_dir or "") / "manifest.json").exists()

    # 2. Re-run pipeline on the same video: must be idempotent and take from cache (cost 0)
    runner2 = PipelineRunner(settings=settings, client=client)
    job2 = runner2.run(target=str(src_video), params=params)

    assert job2.status == "completed"
    assert len(job2.rendered_clips) == 1
    # Gemini API call was NOT repeated because response was cached
    assert sdk.interactions.call_count == 1


def test_pipeline_resumes_after_failure(tmp_path: Path) -> None:
    src_video = tmp_path / "stream_fail.mp4"
    create_synthetic_video(src_video, duration_s=30)

    sdk = MockGenAISDK([])
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", gemini_api_key="mock_key")
    client = GeminiVideoClient(settings, budget=Budget(5.0), sdk=sdk)

    runner = PipelineRunner(settings=settings, client=client)

    # Simulate failure during S3 scan by monkeypatching scan
    orig_scan = client.call
    client.call = lambda **kw: (_ for _ in ()).throw(RuntimeError("Network broke"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="Network broke"):
        runner.run(target=str(src_video), params=JobParams(auto_yes=True))

    # Restore client and resume: runner should pick up from S3_SCAN without redoing S1/S2
    client.call = orig_scan  # type: ignore[method-assign]
    job_resumed = runner.run(target=str(src_video), params=JobParams(auto_yes=True))

    assert job_resumed.status == "completed"
    assert job_resumed.stage == StageEnum.DONE
