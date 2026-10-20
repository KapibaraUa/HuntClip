"""Stage S5: Candidate refinement using Gemini (Pass 2, plan S5)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from cliphunter.config import Settings
from cliphunter.llm.client import GeminiVideoClient
from cliphunter.llm.prompts import load_prompt
from cliphunter.llm.schemas import RefineResponse
from cliphunter.models import Candidate, CandidateStatus, Job
from cliphunter.pipeline.timecodes import parse_ts, to_global
from cliphunter.video.ffmpeg import proxy_cmd, run
from cliphunter.video.snap import snap_candidate_boundaries

log = logging.getLogger(__name__)


def format_refine_prompt(
    template: str,
    candidate: Candidate,
    settings: Settings,
) -> str:
    return template.format(
        working_title=candidate.working_title,
        what_happens=candidate.what_happens or candidate.working_title,
        PAD_BEFORE_S=int(settings.pad_before_s),
        PAD_AFTER_S=int(settings.pad_after_s),
        CLIP_MIN_S=int(settings.clip_min_s),
        CLIP_MAX_S=int(settings.clip_max_s),
    )


def refine_candidate(
    candidate: Candidate,
    job: Job,
    client: GeminiVideoClient,
    settings: Settings,
    work_dir: Path,
) -> Candidate:
    """Runs Stage S5 Refine on a single candidate with a padded window."""
    p = load_prompt("refine")
    sys_prompt = format_refine_prompt(p.body, candidate, settings)

    src_proxy = Path(job.proxy_path or job.original_path or "")
    if not src_proxy.exists():
        raise FileNotFoundError(f"Proxy video not found for refine: {src_proxy}")

    # Calculate window: [start - PAD_BEFORE, end + PAD_AFTER]
    total_dur = job.source.duration_s
    win_start = max(0.0, candidate.start_s - settings.pad_before_s)
    win_end = min(total_dur, candidate.end_s + settings.pad_after_s)
    win_dur = win_end - win_start

    # Cut refine window from proxy
    refine_file = work_dir / f"refine_{candidate.id}.mp4"
    if not refine_file.exists():
        run(
            proxy_cmd(src_proxy, refine_file, start_s=win_start, dur_s=win_dur),
            timeout_s=settings.ffmpeg_timeout_s,
        )

    uri, mime = client.upload(refine_file)
    video_input: dict[str, Any] = {
        "type": "video",
        "uri": uri,
        "mime_type": mime,
        "resolution": settings.media_res_refine,
    }
    if settings.refine_fps != 1.0:
        video_input["processing"] = {"type": "static", "fps": settings.refine_fps}

    def do_call(extra: str = "") -> RefineResponse:
        result = client.call(
            stage="s5_refine",
            mode="file",
            video=video_input,
            prompt="Определи точные границы и вынеси вердикт." + extra,
            system=sys_prompt,
            schema=RefineResponse,
            thinking_level=settings.thinking_level,
            cache_parts={"prompt_version": p.version, "cand_id": candidate.id},
        )
        return RefineResponse.model_validate_json(result.text)

    try:
        resp = do_call()
    except (ValidationError, ValueError) as err:
        log.warning("Refine JSON parse failed for %s, retrying once: %s", candidate.id, err)
        resp = do_call(extra=f"\nПредыдущий ответ был невалидным: {err}. Верни корректный JSON.")

    if resp.verdict == "drop" or not resp.start or not resp.end:
        candidate.status = CandidateStatus.DROPPED
        log.info("Candidate %s dropped during refine: %s", candidate.id, resp.reason)
        return candidate

    # Parse local timecodes and convert to global
    try:
        loc_s = parse_ts(resp.start)
        loc_e = parse_ts(resp.end)
    except ValueError as exc:
        log.warning("Invalid timecode in refine response for %s: %s", candidate.id, exc)
        candidate.status = CandidateStatus.DROPPED
        return candidate

    new_global_start = to_global(loc_s, win_start)
    new_global_end = to_global(loc_e, win_start)
    new_dur = new_global_end - new_global_start

    # Check duration bounds [min, max] with 1 retry
    if not (settings.clip_min_s <= new_dur <= settings.clip_max_s):
        log.info(
            "Refined duration %.1fs out of [%.1f, %.1f], retrying once...",
            new_dur,
            settings.clip_min_s,
            settings.clip_max_s,
        )
        try:
            resp = do_call(
                extra=f"\nДлительность {new_dur:.1f}с не укладывается в [{settings.clip_min_s:.0f}, {settings.clip_max_s:.0f}] с. Скорректируй границы."
            )
            if resp.verdict == "keep" and resp.start and resp.end:
                loc_s = parse_ts(resp.start)
                loc_e = parse_ts(resp.end)
                new_global_start = to_global(loc_s, win_start)
                new_global_end = to_global(loc_e, win_start)
                new_dur = new_global_end - new_global_start
        except Exception:
            pass

    if not (
        settings.clip_min_s - settings.clip_tolerance_s
        <= new_dur
        <= settings.clip_max_s + settings.clip_tolerance_s
    ):
        log.info(
            "Candidate %s duration %.1fs still out of range after retry; dropped",
            candidate.id,
            new_dur,
        )
        candidate.status = CandidateStatus.DROPPED
        return candidate

    # S5b: Boundary snapping
    snapped_s, snapped_e = snap_candidate_boundaries(
        start_s=new_global_start,
        end_s=new_global_end,
        src=src_proxy,
        max_snap_s=settings.snap_s,
    )

    candidate.start_s = snapped_s
    candidate.end_s = snapped_e
    candidate.score = resp.score
    candidate.cuttable_inside = resp.cut_inside
    candidate.status = CandidateStatus.REFINED
    candidate.source_pass = "refine"
    return candidate


def refine_candidates(
    candidates: list[Candidate],
    job: Job,
    client: GeminiVideoClient,
    settings: Settings,
    work_dir: Path,
) -> list[Candidate]:
    """Refines all candidates in job. Returns only kept/refined candidates."""
    refined_list: list[Candidate] = []
    for cand in candidates:
        if cand.status == CandidateStatus.DROPPED:
            continue
        res = refine_candidate(cand, job, client, settings, work_dir)
        if res.status == CandidateStatus.REFINED:
            refined_list.append(res)
    return refined_list
