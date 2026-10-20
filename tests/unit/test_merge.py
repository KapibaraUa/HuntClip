from cliphunter.config import Settings
from cliphunter.models import Candidate, CandidateStatus
from cliphunter.pipeline.merge import (
    apply_coverage_and_rate_limits,
    compute_overlap_ratio,
    deduplicate_candidates,
    filter_by_duration,
    merge_and_dedupe,
)


def make_cand(cid: str, start: float, end: float, score: int = 5, title: str = "t") -> Candidate:
    return Candidate(
        id=cid,
        start_s=start,
        end_s=end,
        category="funny",
        working_title=title,
        score=score,
        status=CandidateStatus.RAW,
        visual_evidence=f"vis_{cid}",
        audio_evidence=f"aud_{cid}",
    )


def test_compute_overlap_ratio() -> None:
    c1 = make_cand("1", 0, 30)  # dur 30
    c2 = make_cand("2", 15, 45)  # dur 30, overlap 15 -> 0.5
    assert compute_overlap_ratio(c1, c2) == 0.5

    c3 = make_cand("3", 10, 20)  # dur 10, completely inside c1 -> overlap 10 / 10 = 1.0
    assert compute_overlap_ratio(c1, c3) == 1.0

    c4 = make_cand("4", 50, 80)
    assert compute_overlap_ratio(c1, c4) == 0.0


def test_deduplicate_candidates_merges_evidence() -> None:
    c1 = make_cand("c1", 100, 140, score=7, title="Winner")
    c2 = make_cand("c2", 115, 145, score=6, title="Loser")  # overlap 25 / 30 = 0.83 >= 0.5

    result = deduplicate_candidates([c2, c1], overlap_threshold=0.5)
    assert len(result) == 1
    winner = result[0]
    assert winner.id == "c1"
    assert winner.score == 7
    assert winner.status == CandidateStatus.MERGED
    assert "vis_c1 | vis_c2" in winner.visual_evidence


def test_filter_by_duration() -> None:
    cands = [
        make_cand("short", 0, 10),  # dur 10
        make_cand("ok", 0, 30),  # dur 30
        make_cand("border_low", 0, 16),  # dur 16 (tol=5, min=20 -> min valid 15)
        make_cand("long", 0, 120),  # dur 120 (max=90 + tol=5 -> 95)
    ]
    filtered = filter_by_duration(cands, min_s=20.0, max_s=90.0, tolerance_s=5.0)
    ids = [c.id for c in filtered]
    assert "ok" in ids and "border_low" in ids
    assert "short" not in ids and "long" not in ids


def test_coverage_and_rate_limits() -> None:
    s = Settings(_env_file=None, max_per_30min=2, max_candidates_per_hour=3)
    # 4 candidates in the first 10 minutes (window of 30 min)
    cands = [
        make_cand("c1", 10, 40, score=10),
        make_cand("c2", 60, 90, score=9),
        make_cand("c3", 120, 150, score=8),
        make_cand("c4", 180, 210, score=7),
    ]
    # Window limit = 2 -> only c1 and c2 should be accepted
    accepted = apply_coverage_and_rate_limits(cands, duration_total_s=3600.0, settings=s)
    assert len(accepted) == 2
    assert [c.id for c in accepted] == ["c1", "c2"]


def test_full_merge_and_dedupe() -> None:
    s = Settings(_env_file=None, clip_min_s=20, clip_max_s=60, clip_tolerance_s=5)
    cands = [
        make_cand("a", 100, 130, score=9),
        make_cand("b", 110, 135, score=6),  # overlap with a -> merges
        make_cand("c", 500, 510, score=8),  # too short (10s) -> filtered
    ]
    res = merge_and_dedupe(cands, total_duration_s=1000.0, settings=s)
    assert len(res) == 1
    assert res[0].id == "a"
