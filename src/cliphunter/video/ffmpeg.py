"""FFmpeg/ffprobe command builders (pure, unit-tested) and a subprocess runner with timeout."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


def proxy_cmd(
    src: Path, dst: Path, start_s: float | None = None, dur_s: float | None = None
) -> list[str]:
    """Proxy for Gemini (plan S1): 720p, 24 fps, CRF ~30, AAC 96k. Optional exact window.

    -ss before -i with re-encoding is frame-accurate, which matters for timecode calibration.
    """
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if start_s is not None:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += ["-i", str(src)]
    if dur_s is not None:
        cmd += ["-t", f"{dur_s:.3f}"]
    cmd += [
        "-vf", "scale=-2:720", "-r", "24",
        "-c:v", "libx264", "-crf", "30", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ac", "1",
        "-movflags", "+faststart", str(dst),
    ]  # fmt: skip
    return cmd


def probe_cmd(path: Path) -> list[str]:
    return [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]  # fmt: skip


def run(cmd: list[str], timeout_s: int) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"timeout after {timeout_s}s: {cmd[0]}") from exc
    if proc.returncode != 0:
        log.error("subprocess failed", extra={"cmd": cmd[0], "stderr": proc.stderr[-2000:]})
        raise FFmpegError(f"{cmd[0]} exited with {proc.returncode}: {proc.stderr[-500:]}")
    return proc


def probe_duration_s(path: Path, timeout_s: int = 60) -> float:
    info = json.loads(run(probe_cmd(path), timeout_s).stdout)
    return float(info["format"]["duration"])
