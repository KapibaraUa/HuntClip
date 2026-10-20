"""Stage S5b: Boundary snapping via audio silence and scene cuts (plan, S5b & Section 9).

Only used for micro-adjusting already found candidate boundaries within SNAP_S (default 1.0s).
Does NOT select moments.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from cliphunter.video.ffmpeg import run

log = logging.getLogger(__name__)

_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")


def detect_silence_points(
    src: Path,
    start_s: float,
    dur_s: float,
    noise_db: float = -30.0,
    min_silence_d: float = 0.2,
    timeout_s: int = 60,
) -> list[float]:
    """Runs ffmpeg silencedetect on a window and returns silence transition points in global seconds."""
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-ss",
        f"{max(0.0, start_s):.3f}",
        "-i",
        str(src),
        "-t",
        f"{dur_s:.3f}",
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence_d}",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = run(cmd, timeout_s=timeout_s)
    except Exception as exc:
        log.warning("silencedetect failed: %s", exc)
        return []

    points: list[float] = []
    base_t = max(0.0, start_s)
    for line in proc.stderr.splitlines():
        m_start = _SILENCE_START_RE.search(line)
        if m_start:
            points.append(base_t + float(m_start.group(1)))
        m_end = _SILENCE_END_RE.search(line)
        if m_end:
            points.append(base_t + float(m_end.group(1)))

    return sorted(set(points))


def detect_scene_cuts(
    src: Path,
    start_s: float,
    dur_s: float,
    threshold: float = 0.3,
    timeout_s: int = 60,
) -> list[float]:
    """Runs ffmpeg scene cut detection on a short window and returns cut points in global seconds."""
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-ss",
        f"{max(0.0, start_s):.3f}",
        "-i",
        str(src),
        "-t",
        f"{dur_s:.3f}",
        "-filter_complex",
        f"select='gt(scene,{threshold})',metadata=print",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = run(cmd, timeout_s=timeout_s)
    except Exception as exc:
        log.warning("scene cut detection failed: %s", exc)
        return []

    pts_re = re.compile(r"pts_time:([0-9.]+)")
    base_t = max(0.0, start_s)
    cuts: list[float] = []
    for line in proc.stderr.splitlines():
        m = pts_re.search(line)
        if m:
            cuts.append(base_t + float(m.group(1)))

    return sorted(set(cuts))


def snap_boundary(
    target_s: float,
    candidates_s: list[float],
    max_snap_s: float = 1.0,
) -> float:
    """Finds the closest point to target_s within max_snap_s. If none within range, keeps target_s."""
    if not candidates_s:
        return target_s

    closest = min(candidates_s, key=lambda pt: abs(pt - target_s))
    if abs(closest - target_s) <= max_snap_s:
        return closest
    return target_s


def snap_candidate_boundaries(
    start_s: float,
    end_s: float,
    src: Path,
    max_snap_s: float = 1.0,
) -> tuple[float, float]:
    """Snaps start and end boundaries to nearby silence pauses or scene cuts within max_snap_s."""
    # Probe audio silence around start and end [-snap, +snap]
    pad = max_snap_s + 0.5
    start_pts = detect_silence_points(src, start_s - pad, pad * 2)
    end_pts = detect_silence_points(src, end_s - pad, pad * 2)

    snapped_start = snap_boundary(start_s, start_pts, max_snap_s)
    snapped_end = snap_boundary(end_s, end_pts, max_snap_s)

    # Ensure start is strictly before end with at least 1s duration
    if snapped_end - snapped_start < 1.0:
        return start_s, end_s

    return round(snapped_start, 3), round(snapped_end, 3)
