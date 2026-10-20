"""Timecode parsing and the single place where chunk-local time becomes global time."""

from __future__ import annotations

import re

_TS = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,3}):(\d{2})(?:[.,](\d{1,3}))?\s*$")


def parse_ts(value: str) -> float:
    """Parse 'MM:SS' or 'HH:MM:SS' (optional fractional seconds) into seconds."""
    m = _TS.match(value)
    if not m:
        raise ValueError(f"invalid timecode: {value!r}")
    h, mm, ss, frac = m.groups()
    if int(ss) >= 60:
        raise ValueError(f"invalid seconds in timecode: {value!r}")
    if h is not None and int(mm) >= 60:
        raise ValueError(f"invalid minutes in timecode: {value!r}")
    seconds = int(h or 0) * 3600 + int(mm) * 60 + int(ss)
    if frac:
        seconds += int(frac) / 10 ** len(frac)
    return float(seconds)


def format_ts(seconds: float) -> str:
    """Format seconds as MM:SS, or HH:MM:SS from one hour on (model boundary only)."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def to_global(local_s: float, chunk_start_s: float) -> float:
    """global = chunk_start + local. The only conversion to the global timeline."""
    return chunk_start_s + local_s


def within_chunk(start_s: float, end_s: float, chunk_len_s: float, slack_s: float = 1.0) -> bool:
    """Local times must satisfy 0 <= start < end <= chunk_len (+ slack for rounding)."""
    return 0 <= start_s < end_s <= chunk_len_s + slack_s
