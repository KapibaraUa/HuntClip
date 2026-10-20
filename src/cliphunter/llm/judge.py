"""Stage S6: Final ranking and filtering using Gemini (Pass 3, plan S6)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from cliphunter.config import Settings
from cliphunter.llm.client import GeminiVideoClient
from cliphunter.llm.prompts import load_prompt
from cliphunter.llm.schemas import JudgeResponse
from cliphunter.models import Candidate, CandidateStatus, Job
from cliphunter.video.ffmpeg import proxy_cmd, run

log = logging.getLogger(__name__)


def prepare_draft_clip(
    candidate: Candidate,
    src_proxy: Path,
    work_dir: Path,
    settings: Settings,
) -> Path:
    """Cuts an unedited draft clip from proxy for the judge pass."""
    out_file = work_dir / f"draft_{candidate.id}.mp4"
    if not out_file.exists():
        run(
            proxy_cmd(src_proxy, out_file, start_s=candidate.start_s, dur_s=candidate.duration_s),
            timeout_s=settings.ffmpeg_timeout_s,
        )
    return out_file


def judge_batch(
    batch: list[Candidate],
    job: Job,
    client: GeminiVideoClient,
    settings: Settings,
    work_dir: Path,
) -> list[Candidate]:
    """Judges a batch of up to 10 candidates in a single multi-video prompt."""
    if not batch:
        return []

    p = load_prompt("judge")
    sys_prompt = p.body.format(N=len(batch))

    src_proxy = Path(job.proxy_path or job.original_path or "")
    if not src_proxy.exists():
        raise FileNotFoundError(f"Source proxy not found: {src_proxy}")

    # Prepare and upload all draft clips
    video_parts: list[dict[str, Any]] = []
    for cand in batch:
        draft_path = prepare_draft_clip(cand, src_proxy, work_dir, settings)
        uri, mime = client.upload(draft_path)
        video_parts.append(
            {
                "type": "video",
                "uri": uri,
                "mime_type": mime,
                "resolution": settings.media_res_scan,  # low resolution for judging is economical
            }
        )

    # In multi-video requests, pass videos followed by prompt
    user_prompt = (
        f"Оцени эти {len(batch)} клипов по критериям hook, emotion, completeness, standalone."
    )

    # Cache key includes candidate IDs
    cand_ids = [c.id for c in batch]
    thinking = settings.thinking_level

    # Direct client call with multi-video input
    result = client.call(
        stage="s6_judge",
        mode="multi_video",
        video=video_parts,
        prompt=user_prompt,
        system=sys_prompt,
        schema=JudgeResponse,
        thinking_level=thinking,
        cache_parts={"prompt_version": p.version, "batch_ids": cand_ids},
    )

    try:
        resp = JudgeResponse.model_validate_json(result.text)
    except Exception as exc:
        log.warning("Judge JSON parse failed: %s; keeping all batch candidates as fallback", exc)
        for cand in batch:
            cand.status = CandidateStatus.ACCEPTED
        return batch

    items_by_idx = {it.clip_index: it for it in resp.items}

    judged: list[Candidate] = []
    for i, cand in enumerate(batch, start=1):
        item = items_by_idx.get(i)
        if item is None:
            # Fallback if model missed an item
            cand.status = CandidateStatus.ACCEPTED
            judged.append(cand)
            continue

        if item.verdict == "drop":
            cand.status = CandidateStatus.DROPPED
            log.info("Candidate %s dropped by judge: %s", cand.id, item.reason)
        else:
            cand.status = CandidateStatus.ACCEPTED
            cand.score = item.score
            cand.source_pass = "judge"
            if item.title_options:
                cand.title_options = item.title_options
            judged.append(cand)

    return judged


def judge_candidates(
    candidates: list[Candidate],
    job: Job,
    client: GeminiVideoClient,
    settings: Settings,
    work_dir: Path,
) -> list[Candidate]:
    """Judges all surviving candidates in chunks of JUDGE_BATCH (up to 10), sorted by score descending."""
    active = [c for c in candidates if c.status != CandidateStatus.DROPPED]
    if not active:
        return []

    batch_size = min(10, max(1, settings.judge_batch))
    accepted_all: list[Candidate] = []

    for i in range(0, len(active), batch_size):
        sub_batch = active[i : i + batch_size]
        accepted = judge_batch(sub_batch, job, client, settings, work_dir)
        accepted_all.extend(accepted)

    # Sort final accepted candidates by score descending
    return sorted(accepted_all, key=lambda c: (c.score, c.duration_s), reverse=True)
