"""Stage S7: Exact frame-accurate cutting from source video with audio fades (plan, Section 9)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from cliphunter.config import Settings
from cliphunter.models import Candidate
from cliphunter.video.ffmpeg import FFmpegError, probe_cmd, run

log = logging.getLogger(__name__)


def cut_command(
    src: Path,
    dst: Path,
    start_s: float,
    dur_s: float,
    use_nvenc: bool = False,
) -> list[str]:
    """Generates FFmpeg command matching Section 9 with audio fades."""
    fade_out_st = max(0.0, dur_s - 0.15)
    audio_filter = f"afade=t=in:st=0:d=0.1,afade=t=out:st={fade_out_st:.3f}:d=0.15"

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start_s:.3f}",
        "-i",
        str(src),
        "-t",
        f"{dur_s:.3f}",
    ]

    if use_nvenc:
        cmd += ["-c:v", "h264_nvenc", "-cq", "19", "-preset", "p4"]
    else:
        cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium"]

    cmd += [
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-af",
        audio_filter,
        "-movflags",
        "+faststart",
        str(dst),
    ]
    return cmd


def verify_rendered_clip(
    path: Path,
    expected_dur_s: float,
    tolerance_s: float = 0.15,
) -> dict[str, Any]:
    """Validates that rendered clip exists, has both video and audio streams, and correct duration."""
    if not path.is_file() or path.stat().st_size == 0:
        raise FFmpegError(f"Rendered clip is empty or does not exist: {path}")

    proc = run(probe_cmd(path), timeout_s=30)
    info = json.loads(proc.stdout)
    streams = info.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    if not has_video:
        raise FFmpegError(f"Rendered clip has no video stream: {path}")
    if not has_audio:
        raise FFmpegError(f"Rendered clip has no audio stream: {path}")

    actual_dur = float(info["format"]["duration"])
    diff = abs(actual_dur - expected_dur_s)
    if diff > tolerance_s:
        log.warning(
            "Rendered duration (%.3fs) differs from expected (%.3fs) by %.3fs (tolerance %.3fs)",
            actual_dur,
            expected_dur_s,
            diff,
            tolerance_s,
        )

    return {
        "file": str(path),
        "size_bytes": path.stat().st_size,
        "actual_duration_s": actual_dur,
        "expected_duration_s": expected_dur_s,
        "duration_diff_s": diff,
        "has_video": has_video,
        "has_audio": has_audio,
    }


def cut_candidate(
    candidate: Candidate,
    src: Path,
    out_dir: Path,
    idx: int,
    settings: Settings,
) -> Path:
    """Cuts a candidate to mp4 and verifies the result."""
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in candidate.working_title)[
        :40
    ]
    out_path = out_dir / f"{idx:02d}_{slug}.mp4"

    cmd = cut_command(
        src=src,
        dst=out_path,
        start_s=candidate.start_s,
        dur_s=candidate.duration_s,
        use_nvenc=settings.use_nvenc,
    )
    run(cmd, timeout_s=settings.ffmpeg_timeout_s)
    verify_rendered_clip(out_path, expected_dur_s=candidate.duration_s)
    return out_path
