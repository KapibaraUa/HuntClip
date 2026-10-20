"""Stage S4: Merge, deduplication, and diversity filtering (plan, S4)."""

from __future__ import annotations

from cliphunter.config import Settings
from cliphunter.models import Candidate, CandidateStatus


def compute_overlap_ratio(a: Candidate, b: Candidate) -> float:
    """Calculates overlap relative to the shorter candidate."""
    inter = max(0.0, min(a.end_s, b.end_s) - max(a.start_s, b.start_s))
    min_dur = min(a.duration_s, b.duration_s)
    if min_dur <= 0:
        return 0.0
    return inter / min_dur


def merge_two_candidates(winner: Candidate, loser: Candidate) -> Candidate:
    """Merges two overlapping candidates, keeping the winner's boundary & score but combining evidence."""
    combined_titles = list(dict.fromkeys(winner.title_options + loser.title_options))
    combined_risks = list(dict.fromkeys(winner.risks + loser.risks))

    def comb(winner_ev: str, loser_ev: str) -> str:
        if not loser_ev or loser_ev == winner_ev:
            return winner_ev
        return f"{winner_ev} | {loser_ev}"

    return Candidate(
        id=winner.id,
        chunk_id=winner.chunk_id,
        start_s=winner.start_s,
        end_s=winner.end_s,
        category=winner.category,
        working_title=winner.working_title,
        score=winner.score,
        status=CandidateStatus.MERGED,
        source_pass=winner.source_pass,
        prompt_version=winner.prompt_version,
        visual_hook=winner.visual_hook or loser.visual_hook,
        hook_text=winner.hook_text or loser.hook_text,
        what_happens=winner.what_happens,
        visual_evidence=comb(winner.visual_evidence, loser.visual_evidence),
        audio_evidence=comb(winner.audio_evidence, loser.audio_evidence),
        why_viral=comb(winner.why_viral, loser.why_viral),
        ending_text=winner.ending_text or loser.ending_text,
        cuttable_inside=winner.cuttable_inside or loser.cuttable_inside,
        title_options=combined_titles,
        risks=combined_risks,
    )


def deduplicate_candidates(
    candidates: list[Candidate], overlap_threshold: float = 0.5
) -> list[Candidate]:
    """Merges overlapping candidates (overlap >= 50% of the shorter one)."""
    if not candidates:
        return []

    # Sort primarily by score descending, then by duration descending
    sorted_cands = sorted(candidates, key=lambda c: (c.score, c.duration_s), reverse=True)
    merged: list[Candidate] = []

    for cand in sorted_cands:
        matched_idx = -1
        for i, existing in enumerate(merged):
            if compute_overlap_ratio(cand, existing) >= overlap_threshold:
                matched_idx = i
                break

        if matched_idx == -1:
            merged.append(cand)
        else:
            # Existing has >= score because of sort order; merge into existing
            merged[matched_idx] = merge_two_candidates(merged[matched_idx], cand)

    return merged


def filter_by_duration(
    candidates: list[Candidate],
    min_s: float,
    max_s: float,
    tolerance_s: float = 5.0,
) -> list[Candidate]:
    """Filters clips strictly inside [min_s - tol, max_s + tol]."""
    low = max(0.0, min_s - tolerance_s)
    high = max_s + tolerance_s
    return [c for c in candidates if low <= c.duration_s <= high]


def apply_coverage_and_rate_limits(
    candidates: list[Candidate],
    duration_total_s: float,
    settings: Settings,
) -> list[Candidate]:
    """Enforces MAX_PER_30MIN and MAX_CANDIDATES_PER_HOUR while maximizing score."""
    if not candidates:
        return []

    # Sort candidates by score descending
    by_score = sorted(candidates, key=lambda c: (c.score, c.duration_s), reverse=True)

    # 1. 30-minute window limits: no more than MAX_PER_30MIN in any 1800s window
    window_s = 1800.0
    accepted: list[Candidate] = []

    for cand in by_score:
        mid_point = (cand.start_s + cand.end_s) / 2.0
        # Check window around mid_point [mid - 900, mid + 900]
        w_start = max(0.0, mid_point - window_s / 2)
        w_end = w_start + window_s
        count_in_window = sum(
            1 for a in accepted if w_start <= ((a.start_s + a.end_s) / 2.0) <= w_end
        )
        if count_in_window < settings.max_per_30min:
            accepted.append(cand)

    # 2. Overall budget per hour limit
    hours = max(1.0, duration_total_s / 3600.0)
    max_total = int(settings.max_candidates_per_hour * hours)
    if len(accepted) > max_total:
        accepted = sorted(accepted, key=lambda c: c.score, reverse=True)[:max_total]

    # Return sorted chronologically by start_s
    return sorted(accepted, key=lambda c: c.start_s)


def merge_and_dedupe(
    candidates: list[Candidate],
    total_duration_s: float,
    settings: Settings,
) -> list[Candidate]:
    """Full S4 pipeline step."""
    deduped = deduplicate_candidates(candidates, overlap_threshold=0.5)
    in_range = filter_by_duration(
        deduped,
        min_s=settings.clip_min_s,
        max_s=settings.clip_max_s,
        tolerance_s=settings.clip_tolerance_s,
    )
    return apply_coverage_and_rate_limits(in_range, total_duration_s, settings)
