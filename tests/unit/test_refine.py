import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cliphunter.config import Settings
from cliphunter.llm.client import CallResult, GeminiVideoClient, Usage
from cliphunter.llm.refine import format_refine_prompt, refine_candidate, refine_candidates
from cliphunter.models import Candidate, CandidateStatus, Job, JobParams, Source, SourceKind


@pytest.fixture
def sample_job(tmp_path: Path) -> Job:
    dummy_proxy = tmp_path / "proxy.mp4"
    dummy_proxy.write_bytes(b"dummy video content")
    return Job(
        id="job_test",
        source=Source(
            id="src_test",
            kind=SourceKind.YOUTUBE_URL,
            url="https://youtu.be/test",
            duration_s=600.0,
        ),
        params=JobParams(),
        proxy_path=str(dummy_proxy),
    )


@pytest.fixture
def sample_candidate() -> Candidate:
    return Candidate(
        id="cand_test_01",
        chunk_idx=0,
        start_s=100.0,
        end_s=130.0,
        category="game_moment",
        working_title="Epic Battle",
        what_happens="Boss is defeated",
        score=8,
    )


def test_format_refine_prompt(sample_candidate: Candidate) -> None:
    template = "Title: {working_title}, Event: {what_happens}, Min: {CLIP_MIN_S}, Max: {CLIP_MAX_S}"
    settings = Settings(_env_file=None, gemini_api_key="mock", clip_min_s=15, clip_max_s=45)
    formatted = format_refine_prompt(template, sample_candidate, settings)
    assert "Epic Battle" in formatted
    assert "Boss is defeated" in formatted
    assert "Min: 15" in formatted
    assert "Max: 45" in formatted


def test_refine_candidate_drop(
    sample_candidate: Candidate, sample_job: Job, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, gemini_api_key="mock")
    mock_client = MagicMock(spec=GeminiVideoClient)
    mock_client.upload.return_value = ("files/mock_uri", "video/mp4")

    drop_resp = json.dumps(
        {
            "verdict": "drop",
            "score": 4,
            "reason": "Not engaging",
        }
    )
    mock_client.call.return_value = CallResult(
        text=drop_resp,
        usage=Usage(100, 50, 0, 0),
        cost_usd=0.001,
        latency_ms=100,
        cache_hit=False,
    )

    with patch("cliphunter.llm.refine.run"):
        res = refine_candidate(sample_candidate, sample_job, mock_client, settings, tmp_path)

    assert res.status == CandidateStatus.DROPPED


def test_refine_candidate_keep_and_snap(
    sample_candidate: Candidate, sample_job: Job, tmp_path: Path
) -> None:
    settings = Settings(
        _env_file=None,
        gemini_api_key="mock",
        pad_before_s=10.0,
        pad_after_s=10.0,
        clip_min_s=15.0,
        clip_max_s=45.0,
        clip_tolerance_s=2.0,
    )
    mock_client = MagicMock(spec=GeminiVideoClient)
    mock_client.upload.return_value = ("files/mock_uri", "video/mp4")

    # In a window of [100 - 10, 130 + 10] = [90, 140] (50s window):
    # LLM says: start is "00:08", end is "00:30" (duration 22s)
    # Global start = 90 + 8 = 98.0, Global end = 90 + 30 = 120.0
    keep_resp = json.dumps(
        {
            "verdict": "keep",
            "start": "00:08",
            "end": "00:30",
            "score": 9,
            "cut_inside": [],
            "reason": "Super clean punchline",
        }
    )
    mock_client.call.return_value = CallResult(
        text=keep_resp,
        usage=Usage(100, 50, 0, 0),
        cost_usd=0.001,
        latency_ms=100,
        cache_hit=False,
    )

    with (
        patch("cliphunter.llm.refine.run"),
        patch(
            "cliphunter.llm.refine.snap_candidate_boundaries",
            return_value=(97.8, 120.2),
        ) as mock_snap,
    ):
        res = refine_candidate(sample_candidate, sample_job, mock_client, settings, tmp_path)

    assert res.status == CandidateStatus.REFINED
    assert res.start_s == 97.8
    assert res.end_s == 120.2
    assert res.score == 9
    assert res.source_pass == "refine"
    mock_snap.assert_called_once()


def test_refine_candidate_duration_retry(
    sample_candidate: Candidate, sample_job: Job, tmp_path: Path
) -> None:
    settings = Settings(
        _env_file=None,
        gemini_api_key="mock",
        pad_before_s=10.0,
        pad_after_s=10.0,
        clip_min_s=15.0,
        clip_max_s=45.0,
        clip_tolerance_s=2.0,
    )
    mock_client = MagicMock(spec=GeminiVideoClient)
    mock_client.upload.return_value = ("files/mock_uri", "video/mp4")

    # Call 1: duration too short (5s: 00:05 to 00:10)
    resp1 = json.dumps(
        {
            "verdict": "keep",
            "start": "00:05",
            "end": "00:10",
            "score": 7,
            "cut_inside": [],
            "reason": "Too brief",
        }
    )
    # Call 2 (retry): valid duration (20s: 00:05 to 00:25)
    resp2 = json.dumps(
        {
            "verdict": "keep",
            "start": "00:05",
            "end": "00:25",
            "score": 9,
            "cut_inside": [],
            "reason": "Expanded context",
        }
    )

    mock_client.call.side_effect = [
        CallResult(
            text=resp1, usage=Usage(10, 10, 0, 0), cost_usd=0.001, latency_ms=100, cache_hit=False
        ),
        CallResult(
            text=resp2, usage=Usage(10, 10, 0, 0), cost_usd=0.001, latency_ms=100, cache_hit=False
        ),
    ]

    with (
        patch("cliphunter.llm.refine.run"),
        patch(
            "cliphunter.llm.refine.snap_candidate_boundaries",
            return_value=(95.0, 115.0),
        ),
    ):
        res = refine_candidate(sample_candidate, sample_job, mock_client, settings, tmp_path)

    assert mock_client.call.call_count == 2
    assert res.status == CandidateStatus.REFINED
    assert res.start_s == 95.0
    assert res.end_s == 115.0


def test_refine_candidates_batch_filtering(sample_job: Job, tmp_path: Path) -> None:
    c1 = Candidate(
        id="c1",
        chunk_idx=0,
        start_s=10.0,
        end_s=30.0,
        category="game_moment",
        working_title="T1",
        score=5,
        status=CandidateStatus.DROPPED,
    )
    c2 = Candidate(
        id="c2",
        chunk_idx=0,
        start_s=40.0,
        end_s=65.0,
        category="funny",
        working_title="T2",
        score=7,
        status=CandidateStatus.RAW,
    )

    mock_client = MagicMock(spec=GeminiVideoClient)
    settings = Settings(_env_file=None, gemini_api_key="mock")

    refined_c2 = Candidate(
        id="c2",
        chunk_idx=0,
        start_s=42.0,
        end_s=63.0,
        category="funny",
        working_title="T2",
        score=8,
        status=CandidateStatus.REFINED,
    )

    with patch("cliphunter.llm.refine.refine_candidate", return_value=refined_c2) as mock_single:
        results = refine_candidates([c1, c2], sample_job, mock_client, settings, tmp_path)

    # c1 was dropped so refine_candidate was only called for c2
    assert mock_single.call_count == 1
    assert len(results) == 1
    assert results[0].id == "c2"
    assert results[0].status == CandidateStatus.REFINED
