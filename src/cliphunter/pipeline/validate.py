"""Candidate validation and sanitization against chunk boundaries and clip limits."""

from __future__ import annotations

import logging

from cliphunter.config import Settings
from cliphunter.llm.schemas import ScanClip
from cliphunter.models import Candidate, CandidateStatus
from cliphunter.pipeline.timecodes import parse_ts, to_global, within_chunk

log = logging.getLogger(__name__)


def validate_scan_clip(
    clip_data: ScanClip,
    chunk_start_s: float,
    chunk_len_s: float,
    chunk_id: str,
    prompt_version: int,
    settings: Settings,
) -> Candidate | None:
    """Validates a raw ScanClip from LLM, parses timecodes, converts to global time."""
    try:
        start_local = parse_ts(clip_data.start)
        end_local = parse_ts(clip_data.end)
    except ValueError as exc:
        log.warning(
            "Invalid timecode format in clip: %s", exc, extra={"clip": clip_data.working_title}
        )
        return None

    if not within_chunk(start_local, end_local, chunk_len_s):
        log.warning(
            "Clip timestamps outside chunk boundaries [%s, %s] for len=%s",
            start_local,
            end_local,
            chunk_len_s,
            extra={"clip": clip_data.working_title},
        )
        return None

    global_start = to_global(start_local, chunk_start_s)
    global_end = to_global(end_local, chunk_start_s)
    dur = global_end - global_start

    # Basic duration sanity check (clip should be positive length)
    if dur <= 0:
        return None

    cid = f"{chunk_id}_{int(global_start):05d}_{int(global_end):05d}"
    return Candidate(
        id=cid,
        chunk_id=chunk_id,
        start_s=round(global_start, 3),
        end_s=round(global_end, 3),
        category=clip_data.category,
        working_title=clip_data.working_title,
        score=clip_data.score,
        status=CandidateStatus.RAW,
        source_pass="scan",
        prompt_version=prompt_version,
        visual_hook=clip_data.visual_hook,
        hook_text=clip_data.hook_text,
        what_happens=clip_data.what_happens,
        visual_evidence=clip_data.visual_evidence,
        audio_evidence=clip_data.audio_evidence,
        why_viral=clip_data.why_viral,
        ending_text=clip_data.ending_text,
        cuttable_inside=clip_data.cuttable_inside,
        title_options=clip_data.title_options,
        risks=clip_data.risks,
    )
