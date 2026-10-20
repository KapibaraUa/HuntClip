"""Stage S3: Scan runner using Gemini (Pass 1)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from cliphunter.config import Settings
from cliphunter.llm.client import GeminiVideoClient
from cliphunter.llm.prompts import load_prompt, scan_user_message
from cliphunter.llm.schemas import ScanResponse
from cliphunter.models import Candidate, Chunk, ChunkStatus, Job, SourceKind
from cliphunter.pipeline.validate import validate_scan_clip
from cliphunter.video.ffmpeg import proxy_cmd, run

log = logging.getLogger(__name__)


def prepare_chunk_video_input(
    chunk: Chunk,
    job: Job,
    client: GeminiVideoClient,
    settings: Settings,
    work_dir: Path,
) -> dict[str, Any]:
    """Prepares the video part of the API request: url-mode or file-mode."""
    source = job.source
    res_scan = settings.media_res_scan

    # URL-mode: only for YouTube when URL_OFFSETS_OK is explicitly True
    if settings.url_offsets_ok and source.kind == SourceKind.YOUTUBE_URL and source.url:
        return {
            "type": "video",
            "uri": source.url,
            "resolution": res_scan,
            "processing": {
                "type": "static",
                "start_offset": int(chunk.start_s),
                "end_offset": int(chunk.end_s),
            },
        }

    # File-mode: cut chunk proxy, upload to Files API
    chunk_proxy_file = work_dir / f"chunk_{chunk.idx:03d}.mp4"
    if not chunk_proxy_file.exists():
        # Cut chunk directly from proxy (or original if no proxy)
        src_path = Path(job.proxy_path or job.original_path or "")
        if not src_path.exists():
            raise FileNotFoundError(f"Source video for chunking not found: {src_path}")
        run(
            proxy_cmd(src_path, chunk_proxy_file, start_s=chunk.start_s, dur_s=chunk.duration_s),
            timeout_s=settings.ffmpeg_timeout_s,
        )

    uri, mime = client.upload(chunk_proxy_file)
    chunk.file_uri = uri
    return {
        "type": "video",
        "uri": uri,
        "mime_type": mime,
        "resolution": res_scan,
    }


def scan_chunk(
    chunk: Chunk,
    job: Job,
    client: GeminiVideoClient,
    settings: Settings,
    work_dir: Path,
) -> list[Candidate]:
    """Scans a single chunk with Gemini Pass 1 (prompts/scan.md). Handles 1 retry on invalid JSON."""
    p = load_prompt("scan")
    profile = job.profile
    system_text = p.body

    # Calculate max candidates for this chunk (proportional to hour limit)
    duration_hrs = chunk.duration_s / 3600.0
    max_cands = max(3, int(settings.max_candidates_per_hour * duration_hrs) + 2)

    user_text = scan_user_message(
        channel=source_channel(job),
        game=profile.game or "Стрим",
        language=profile.language,
        clip_min_s=job.params.clip_min_s,
        clip_max_s=job.params.clip_max_s,
        max_candidates=max_cands,
        prompt_addon=profile.prompt_addon,
    )

    video_input = prepare_chunk_video_input(chunk, job, client, settings, work_dir)

    def do_call(extra_prompt: str = "") -> ScanResponse:
        result = client.call(
            stage="s3_scan",
            mode="url" if video_input.get("processing") else "file",
            video=video_input,
            prompt=user_text + extra_prompt,
            system=system_text,
            schema=ScanResponse,
            thinking_level=settings.thinking_level,
            cache_parts={"prompt_version": p.version, "chunk_idx": chunk.idx},
        )
        chunk.tokens_in += result.usage.billed_in
        chunk.tokens_out += result.usage.billed_out
        chunk.cost_usd += result.cost_usd
        return ScanResponse.model_validate_json(result.text)

    try:
        try:
            resp = do_call()
        except (ValidationError, ValueError) as err:
            log.warning("Scan JSON parse failed for chunk %d, retrying once: %s", chunk.idx, err)
            resp = do_call(
                extra_prompt=f"\nПредыдущий ответ был невалидным: {err}. Верни строгий JSON."
            )
    except Exception as exc:
        err_msg = str(exc)
        if "SAFETY" in err_msg.upper() or "BLOCK" in err_msg.upper():
            log.warning("Chunk %d blocked by content safety: %s", chunk.idx, err_msg)
            chunk.status = ChunkStatus.PARTIAL
            chunk.error = f"Safety block: {err_msg}"
            return []
        chunk.status = ChunkStatus.FAILED
        chunk.error = err_msg
        raise

    chunk.status = ChunkStatus.DONE
    candidates: list[Candidate] = []
    for c in resp.clips:
        validated = validate_scan_clip(
            clip_data=c,
            chunk_start_s=chunk.start_s,
            chunk_len_s=chunk.duration_s,
            chunk_id=chunk.id,
            prompt_version=p.version,
            settings=settings,
        )
        if validated is not None:
            candidates.append(validated)

    return candidates


def source_channel(job: Job) -> str:
    return job.source.channel if job.source.channel != "Unknown" else job.profile.name
