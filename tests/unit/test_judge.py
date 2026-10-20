import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cliphunter.config import Settings
from cliphunter.llm.client import CallResult, GeminiVideoClient, Usage
from cliphunter.llm.judge import judge_batch, judge_candidates, prepare_draft_clip
from cliphunter.models import Candidate, CandidateStatus, Job, JobParams, Source, SourceKind


@pytest.fixture
def sample_job(tmp_path: Path) -> Job:
    dummy_proxy = tmp_path / "proxy.mp4"
    dummy_proxy.write_bytes(b"dummy video content")
    return Job(
        id="job_judge_test",
        source=Source(
            id="src_test",
            kind=SourceKind.FILE,
            duration_s=300.0,
            path=str(dummy_proxy),
        ),
        params=JobParams(),
        proxy_path=str(dummy_proxy),
    )


def test_prepare_draft_clip(sample_job: Job, tmp_path: Path) -> None:
    settings = Settings(_env_file=None, gemini_api_key="mock")
    cand = Candidate(
        id="c1",
        chunk_idx=0,
        start_s=10.0,
        end_s=25.0,
        category="funny",
        working_title="Funny Clip",
        score=8,
    )
    src_proxy = Path(sample_job.proxy_path or "")

    with patch("cliphunter.llm.judge.run") as mock_run:
        out = prepare_draft_clip(cand, src_proxy, tmp_path, settings)

    assert out == tmp_path / "draft_c1.mp4"
    mock_run.assert_called_once()


def test_judge_batch_success(sample_job: Job, tmp_path: Path) -> None:
    settings = Settings(_env_file=None, gemini_api_key="mock")
    c1 = Candidate(
        id="c1",
        chunk_idx=0,
        start_s=10.0,
        end_s=25.0,
        category="funny",
        working_title="Funny Clip",
        score=7,
    )
    c2 = Candidate(
        id="c2",
        chunk_idx=0,
        start_s=50.0,
        end_s=70.0,
        category="story",
        working_title="Boring Story",
        score=6,
    )

    mock_client = MagicMock(spec=GeminiVideoClient)
    mock_client.upload.side_effect = [
        ("files/c1_uri", "video/mp4"),
        ("files/c2_uri", "video/mp4"),
    ]

    judge_resp = json.dumps(
        {
            "items": [
                {
                    "clip_index": 1,
                    "verdict": "keep",
                    "score": 9,
                    "hook": 9,
                    "emotion": 8,
                    "completeness": 9,
                    "standalone": 9,
                    "reason": "Top tier comedy",
                    "title_options": ["Hilarious Moment", "Streamer Comedy"],
                },
                {
                    "clip_index": 2,
                    "verdict": "drop",
                    "score": 4,
                    "hook": 4,
                    "emotion": 3,
                    "completeness": 5,
                    "standalone": 4,
                    "reason": "Too slow and uninteresting",
                    "title_options": [],
                },
            ]
        }
    )
    mock_client.call.return_value = CallResult(
        text=judge_resp,
        usage=Usage(100, 50, 0, 0),
        cost_usd=0.002,
        latency_ms=150,
        cache_hit=False,
    )

    with patch(
        "cliphunter.llm.judge.prepare_draft_clip",
        side_effect=lambda c, *args: tmp_path / f"{c.id}.mp4",
    ):
        accepted = judge_batch([c1, c2], sample_job, mock_client, settings, tmp_path)

    # c1 kept with score 9 and updated title_options, c2 dropped
    assert len(accepted) == 1
    assert accepted[0].id == "c1"
    assert accepted[0].score == 9
    assert accepted[0].source_pass == "judge"
    assert accepted[0].status == CandidateStatus.ACCEPTED
    assert accepted[0].title_options == ["Hilarious Moment", "Streamer Comedy"]
    assert c2.status == CandidateStatus.DROPPED


def test_judge_batch_parse_fallback(sample_job: Job, tmp_path: Path) -> None:
    settings = Settings(_env_file=None, gemini_api_key="mock")
    c1 = Candidate(
        id="c1",
        chunk_idx=0,
        start_s=10.0,
        end_s=25.0,
        category="funny",
        working_title="Funny Clip",
        score=7,
    )

    mock_client = MagicMock(spec=GeminiVideoClient)
    mock_client.upload.return_value = ("files/c1_uri", "video/mp4")
    mock_client.call.return_value = CallResult(
        text="Invalid JSON from LLM",
        usage=Usage(100, 50, 0, 0),
        cost_usd=0.002,
        latency_ms=150,
        cache_hit=False,
    )

    with patch("cliphunter.llm.judge.prepare_draft_clip", return_value=tmp_path / "c1.mp4"):
        accepted = judge_batch([c1], sample_job, mock_client, settings, tmp_path)

    # Falls back gracefully so candidates are not lost
    assert len(accepted) == 1
    assert accepted[0].status == CandidateStatus.ACCEPTED


def test_judge_candidates_sorting_and_batching(sample_job: Job, tmp_path: Path) -> None:
    settings = Settings(_env_file=None, gemini_api_key="mock", judge_batch=2)
    mock_client = MagicMock(spec=GeminiVideoClient)

    # Create 3 candidates
    candidates = [
        Candidate(
            id="c1",
            chunk_idx=0,
            start_s=10.0,
            end_s=30.0,
            category="funny",
            working_title="C1",
            score=7,
        ),
        Candidate(
            id="c2",
            chunk_idx=0,
            start_s=40.0,
            end_s=60.0,
            category="emotion",
            working_title="C2",
            score=9,
        ),
        Candidate(
            id="c3",
            chunk_idx=0,
            start_s=70.0,
            end_s=105.0,
            category="story",
            working_title="C3",
            score=8,
        ),
        Candidate(
            id="c4_dropped",
            chunk_idx=0,
            start_s=120.0,
            end_s=140.0,
            category="quote",
            working_title="C4",
            score=4,
            status=CandidateStatus.DROPPED,
        ),
    ]

    # Batch 1 (c1, c2) -> c2 score 9, c1 score 7
    # Batch 2 (c3) -> c3 score 8
    # When judge_batch is called, return items directly
    def fake_judge_batch(batch: list[Candidate], *args: Any) -> list[Candidate]:
        for c in batch:
            c.status = CandidateStatus.ACCEPTED
        return batch

    with patch("cliphunter.llm.judge.judge_batch", side_effect=fake_judge_batch) as mock_jb:
        sorted_res = judge_candidates(candidates, sample_job, mock_client, settings, tmp_path)

    # judge_batch called twice (batch of 2, then batch of 1; c4 was skipped because already dropped)
    assert mock_jb.call_count == 2
    # Result is sorted by score descending: c2 (9), c3 (8), c1 (7)
    assert [c.id for c in sorted_res] == ["c2", "c3", "c1"]
