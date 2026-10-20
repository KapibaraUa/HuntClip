"""Chunk planning (plan, Stage S2).

Calculates chunk boundaries based on CHUNK_S, OVERLAP_S, and SHORT_VIDEO_MAX_S.
Verifies that chunk length respects the LLM context share limit.
"""

from __future__ import annotations

from cliphunter.config import Settings
from cliphunter.models import Chunk, ChunkStatus


class ContextLimitExceeded(ValueError):
    """Raised when chunk duration would exceed the LLM context window safety share."""


def plan_chunks(duration_s: float, settings: Settings, job_id: str = "job") -> list[Chunk]:
    if duration_s <= 0:
        return []

    # Context window check (from plan S2: CHUNK_S * tokens_per_sec < 60% of context)
    if not settings.chunk_fits_context(settings.media_res_scan):
        raise ContextLimitExceeded(
            f"CHUNK_S={settings.chunk_s} exceeds 60% context window limit at {settings.media_res_scan} resolution"
        )

    # Short video: single chunk
    if duration_s <= settings.short_video_max_s:
        return [
            Chunk(
                id=f"{job_id}_c00",
                idx=0,
                start_s=0.0,
                end_s=round(duration_s, 3),
                status=ChunkStatus.PENDING,
            )
        ]

    chunk_s = float(settings.chunk_s)
    overlap_s = float(settings.overlap_s)
    step_s = chunk_s - overlap_s
    if step_s <= 0:
        raise ValueError(f"step_s ({step_s}) must be strictly positive (CHUNK_S > OVERLAP_S)")

    chunks: list[Chunk] = []
    idx = 0
    cur_start = 0.0

    while cur_start < duration_s:
        cur_end = min(cur_start + chunk_s, duration_s)
        # Avoid creating a tiny trailing chunk that is smaller than half overlap
        if duration_s - cur_end < (overlap_s / 2.0):
            cur_end = duration_s

        chunks.append(
            Chunk(
                id=f"{job_id}_c{idx:02d}",
                idx=idx,
                start_s=round(cur_start, 3),
                end_s=round(cur_end, 3),
                status=ChunkStatus.PENDING,
            )
        )
        if cur_end >= duration_s:
            break

        idx += 1
        cur_start += step_s

    return chunks
