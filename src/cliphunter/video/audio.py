"""Audio processing filters for FFmpeg: EBU R128 loudness normalization and edge fades (plan, Section 9)."""

from __future__ import annotations


def build_loudnorm_filter(
    integrated_lufs: float = -16.0,
    true_peak_db: float = -1.5,
    lra: float = 11.0,
) -> str:
    """Returns FFmpeg EBU R128 loudnorm filter string (`loudnorm=I=-16:TP=-1.5:LRA=11`)."""
    return f"loudnorm=I={integrated_lufs:g}:TP={true_peak_db:g}:LRA={lra:g}"


def build_afade_filter(
    dur_s: float,
    fade_in_s: float = 0.10,
    fade_out_s: float = 0.15,
) -> str:
    """Returns FFmpeg audio fade-in and fade-out filter string to eliminate splice clicks."""
    fade_out_st = max(0.0, dur_s - fade_out_s)
    return f"afade=t=in:st=0:d={fade_in_s:g},afade=t=out:st={fade_out_st:.3f}:d={fade_out_s:g}"


def build_audio_filter(dur_s: float, normalize_loudness: bool = True) -> str:
    """Combines EBU R128 loudnorm normalization with in/out audio fades."""
    fades = build_afade_filter(dur_s)
    if normalize_loudness:
        return f"{build_loudnorm_filter()},{fades}"
    return fades
