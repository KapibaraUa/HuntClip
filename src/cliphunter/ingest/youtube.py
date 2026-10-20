"""yt-dlp wrapper: metadata probe and download of a time section."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def ytdlp_base() -> list[str]:
    # Run via the current interpreter so the project-local venv is used, not a global install.
    return [sys.executable, "-m", "yt_dlp"]


def info_cmd(url: str) -> list[str]:
    return [*ytdlp_base(), "--simulate", "--no-playlist", "--dump-single-json", url]


def section_cmd(url: str, start_s: float, end_s: float, out_template: Path) -> list[str]:
    return [
        *ytdlp_base(), "--no-playlist",
        "-f", "bv*[height<=720]+ba/b[height<=720]/b",
        "--download-sections", f"*{start_s:.0f}-{end_s:.0f}",
        "--force-keyframes-at-cuts",
        "--merge-output-format", "mp4",
        "-o", str(out_template), url,
    ]  # fmt: skip


def fetch_info(url: str, timeout_s: int = 120) -> dict[str, object]:
    out = subprocess.run(
        info_cmd(url), capture_output=True, text=True, timeout=timeout_s, check=True
    ).stdout
    data: dict[str, object] = json.loads(out)
    return data


def download_section(
    url: str, start_s: float, end_s: float, dst: Path, timeout_s: int = 1800
) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(section_cmd(url, start_s, end_s, dst), check=True, timeout=timeout_s)
    return dst
