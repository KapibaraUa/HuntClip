"""Unified ingest probe: detects YouTube URL, local file, or other web video."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from cliphunter.ingest.files import probe_file
from cliphunter.ingest.youtube import fetch_info
from cliphunter.models import Source, SourceKind

_YT_REGEX = re.compile(r"^(https?://)?(www\.|m\.)?(youtube\.com|youtu\.be)/.+$")


def is_youtube_url(url: str) -> bool:
    return bool(_YT_REGEX.match(url.strip()))


def probe_source(target: str) -> Source:
    target_clean = target.strip()
    target_path = Path(target_clean)

    if target_path.is_file():
        return probe_file(target_path)

    if is_youtube_url(target_clean):
        info = fetch_info(target_clean)
        dur = info.get("duration")
        if dur is None or not isinstance(dur, (int, float)):
            raise ValueError(f"Could not determine duration for YouTube URL: {target_clean}")
        raw_id = str(info.get("id") or hashlib.sha256(target_clean.encode()).hexdigest()[:12])
        return Source(
            id=f"yt_{raw_id}",
            kind=SourceKind.YOUTUBE_URL,
            url=target_clean,
            duration_s=float(dur),
            title=str(info.get("title") or "YouTube Video"),
            channel=str(info.get("uploader") or info.get("channel") or "Unknown Channel"),
            sha256=None,
        )

    # Other URL (e.g., Twitch, direct link)
    info = fetch_info(target_clean)
    dur = info.get("duration")
    if dur is None or not isinstance(dur, (int, float)):
        raise ValueError(f"Could not determine duration for URL: {target_clean}")
    raw_id = str(info.get("id") or hashlib.sha256(target_clean.encode()).hexdigest()[:12])
    return Source(
        id=f"url_{raw_id}",
        kind=SourceKind.OTHER_URL,
        url=target_clean,
        duration_s=float(dur),
        title=str(info.get("title") or "Web Video"),
        channel=str(info.get("uploader") or "Unknown"),
        sha256=None,
    )
