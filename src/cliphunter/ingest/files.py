"""Local file inspection: duration via ffprobe and SHA256 checksum."""

from __future__ import annotations

import hashlib
from pathlib import Path

from cliphunter.models import Source, SourceKind
from cliphunter.video.ffmpeg import probe_duration_s


def compute_sha256(path: Path, block_size: int = 65536) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(block_size):
            hasher.update(block)
    return hasher.hexdigest()


def probe_file(path: Path, compute_hash: bool = True) -> Source:
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    duration = probe_duration_s(path)
    file_id = path.stem
    sha256_val = compute_sha256(path) if compute_hash else None
    if sha256_val:
        file_id = sha256_val[:16]

    return Source(
        id=file_id,
        kind=SourceKind.FILE,
        path=str(path.resolve()),
        duration_s=duration,
        title=path.stem,
        channel="local",
        sha256=sha256_val,
    )
