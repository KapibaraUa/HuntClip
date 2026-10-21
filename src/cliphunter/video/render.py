"""Unified video render engine combining layouts, audio loudnorm, title hooks, and templates (plan, Section 9)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from cliphunter.config import Settings
from cliphunter.models import Candidate, Profile
from cliphunter.video.ffmpeg import FFmpegError, probe_cmd, run
from cliphunter.video.layouts import get_layout_filter
from cliphunter.video.template import build_title_hook_filter

log = logging.getLogger(__name__)


def build_audio_filter(dur_s: float, normalize_loudness: bool = True) -> str:
    """Combines EBU R128 loudnorm normalization with in/out audio fades."""
    fade_out_st = max(0.0, dur_s - 0.15)
    fades = f"afade=t=in:st=0:d=0.1,afade=t=out:st={fade_out_st:.3f}:d=0.15"
    if normalize_loudness:
        # Integrated loudness -16 LUFS, true peak -1.5 dB, loudness range 11
        return f"loudnorm=I=-16:TP=-1.5:LRA=11,{fades}"
    return fades


def build_render_command(
    src: Path,
    dst: Path,
    start_s: float,
    dur_s: float,
    layout: str = "original",
    profile: Profile | None = None,
    title: str | None = None,
    ass_subtitles: Path | None = None,
    use_nvenc: bool = False,
    normalize_audio: bool = True,
) -> list[str]:
    """Assembles a full FFmpeg pipeline command for any layout with loudnorm and branding."""
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

    # Video filters
    layout_filter = get_layout_filter(layout, profile)
    video_filters: list[str] = []

    # If title hook is requested and layout is vertical, add title overlay
    if title:
        title_filter = build_title_hook_filter(
            title=title,
            start_t=0.2,
            end_t=min(3.8, max(1.0, dur_s - 0.5)),
            font_size=44 if "9x16" in layout else 36,
            y_fraction=0.10 if "9x16" in layout else 0.08,
        )
        if title_filter:
            video_filters.append(title_filter)

    # Subtitles overlay
    if ass_subtitles and ass_subtitles.is_file():
        clean_ass = str(ass_subtitles).replace("\\", "/").replace(":", "\\:")
        video_filters.append(f"ass='{clean_ass}'")

    # Combine layout filtergraph with sequential filters
    if layout_filter:
        if video_filters:
            chained = ",".join(video_filters)
            # Check if layout filter uses complex filter syntax with semicolons
            if ";" in layout_filter:
                filter_graph = f"{layout_filter},{chained}"
            else:
                filter_graph = f"{layout_filter},{chained}"
        else:
            filter_graph = layout_filter
        cmd += ["-filter_complex", filter_graph]
    elif video_filters:
        cmd += ["-vf", ",".join(video_filters)]

    # Video encoder
    if use_nvenc:
        cmd += ["-c:v", "h264_nvenc", "-cq", "19", "-preset", "p4"]
    else:
        cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "medium"]

    cmd += ["-pix_fmt", "yuv420p"]

    # Audio encoder & filter
    audio_filt = build_audio_filter(dur_s, normalize_loudness=normalize_audio)
    cmd += [
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-af",
        audio_filt,
        "-movflags",
        "+faststart",
        str(dst),
    ]

    return cmd


def verify_rendered_clip_dimensions(
    path: Path,
    expected_dur_s: float,
    layout: str = "original",
    tolerance_s: float = 0.15,
) -> dict[str, Any]:
    """Verifies that the rendered clip has valid video, audio, duration, and resolution."""
    if not path.is_file() or path.stat().st_size == 0:
        raise FFmpegError(f"Rendered clip is empty or missing: {path}")

    proc = run(probe_cmd(path), timeout_s=30)
    info = json.loads(proc.stdout)
    streams = info.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if not video_stream:
        raise FFmpegError(f"Rendered clip has no video stream: {path}")
    if not audio_stream:
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

    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))

    if ("9x16" in layout or "split" in layout or "blur" in layout) and (
        width != 1080 or height != 1920
    ):
        log.warning(
            "Vertical layout '%s' produced resolution %dx%d (expected 1080x1920)",
            layout,
            width,
            height,
        )

    return {
        "file": str(path),
        "size_bytes": path.stat().st_size,
        "width": width,
        "height": height,
        "actual_duration_s": actual_dur,
        "expected_duration_s": expected_dur_s,
        "duration_diff_s": diff,
    }


def render_candidate(
    candidate: Candidate,
    src: Path,
    out_dir: Path,
    idx: int,
    layout: str = "original",
    profile: Profile | None = None,
    settings: Settings | None = None,
    title: str | None = None,
    ass_subtitles: Path | None = None,
) -> Path:
    """Renders a candidate moment into final MP4 using specified layout and templates."""
    s = settings or Settings()
    out_dir.mkdir(parents=True, exist_ok=True)

    title_text = title or candidate.working_title
    slug = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in title_text)[:40]
    out_path = out_dir / f"{idx:02d}_{slug}_{layout}.mp4"

    cmd = build_render_command(
        src=src,
        dst=out_path,
        start_s=candidate.start_s,
        dur_s=candidate.duration_s,
        layout=layout,
        profile=profile,
        title=title_text,
        ass_subtitles=ass_subtitles,
        use_nvenc=s.use_nvenc,
        normalize_audio=True,
    )

    run(cmd, timeout_s=s.ffmpeg_timeout_s)
    verify_rendered_clip_dimensions(
        out_path,
        expected_dur_s=candidate.duration_s,
        layout=layout,
    )
    return out_path
