import pytest

from cliphunter.config import Settings
from cliphunter.pipeline.chunking import ContextLimitExceeded, plan_chunks


def test_plan_chunks_short_video() -> None:
    s = Settings(_env_file=None, chunk_s=2700, short_video_max_s=2700)
    chunks = plan_chunks(1800.0, s, job_id="job1")
    assert len(chunks) == 1
    assert chunks[0].idx == 0
    assert chunks[0].start_s == 0.0
    assert chunks[0].end_s == 1800.0
    assert chunks[0].id == "job1_c00"


def test_plan_chunks_long_video_with_overlap() -> None:
    # 5400s video (90 min), chunk 2700s, overlap 120s, step 2580s
    s = Settings(_env_file=None, chunk_s=2700, overlap_s=120, short_video_max_s=2700)
    chunks = plan_chunks(5400.0, s, job_id="job2")

    # chunk 0: [0, 2700]
    # chunk 1: [2580, 5280]
    # chunk 2: [5160, 5400]
    assert len(chunks) == 3
    assert (chunks[0].start_s, chunks[0].end_s) == (0.0, 2700.0)
    assert (chunks[1].start_s, chunks[1].end_s) == (2580.0, 5280.0)
    assert (chunks[2].start_s, chunks[2].end_s) == (5160.0, 5400.0)


def test_plan_chunks_stretches_tiny_trailing_chunk() -> None:
    # 2750s video, chunk 2700s, overlap 120s. Leftover is 50s (< 120s)
    s = Settings(_env_file=None, chunk_s=2700, overlap_s=120, short_video_max_s=2700)
    chunks = plan_chunks(2750.0, s, job_id="job3")
    assert len(chunks) == 1
    assert chunks[0].end_s == 2750.0


def test_plan_chunks_zero_duration() -> None:
    s = Settings(_env_file=None)
    assert plan_chunks(0.0, s) == []


def test_plan_chunks_context_exceeded() -> None:
    # Force context share limit check failure
    s = Settings(_env_file=None, chunk_s=10000, context_tokens=10000, context_share_max=0.5)
    with pytest.raises(ContextLimitExceeded):
        plan_chunks(3600.0, s)
