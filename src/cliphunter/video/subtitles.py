"""Subtitle generation module (plan, Section 9).

Generates styled .ass subtitles (karaoke / word-level highlight) from clip audio using faster-whisper.
Disabled by default (SUBTITLES=false).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from cliphunter.config import Settings

log = logging.getLogger(__name__)


@dataclass
class SubtitleWord:
    word: str
    start_s: float
    end_s: float


def format_ass_timestamp(seconds: float) -> str:
    """Formats seconds into ASS timestamp format: H:MM:SS.cc"""
    s = max(0.0, seconds)
    hours = int(s // 3600)
    minutes = int((s % 3600) // 60)
    secs = int(s % 60)
    centis = round((s - int(s)) * 100)
    if centis >= 100:
        secs += 1
        centis = 0
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def generate_ass_file(
    words: list[SubtitleWord],
    out_path: Path,
    words_per_line: int = 4,
    font_size: int = 56,
) -> Path:
    """Generates an Advanced SubStation Alpha (.ass) file with word-by-word highlight."""
    header = """[Script Info]
Title: ClipHunter Subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,52,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3.5,1.5,2,40,40,240,1
Style: Highlight,Arial,54,&H0000F0FF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4.0,2.0,2,40,40,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = [header]

    # Group words into short readable chunks (lines)
    chunks = [words[i : i + words_per_line] for i in range(0, len(words), words_per_line)]

    for chunk in chunks:
        if not chunk:
            continue

        # For each word in chunk, emit dialogue where that active word is highlighted
        for active_idx, target_word in enumerate(chunk):
            word_start = target_word.start_s
            word_end = target_word.end_s

            text_parts: list[str] = []
            for j, w in enumerate(chunk):
                if j == active_idx:
                    text_parts.append(f"{{\\c&H0034D399&}}{w.word}{{\\c&H00FFFFFF&}}")
                else:
                    text_parts.append(w.word)

            dialogue_text = " ".join(text_parts)
            lines.append(
                f"Dialogue: 0,{format_ass_timestamp(word_start)},{format_ass_timestamp(word_end)},"
                f"Default,,0,0,0,,{dialogue_text}\n"
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path


def transcribe_clip_subtitles(
    clip_path: Path,
    out_ass_path: Path,
    settings: Settings,
    language: str = "ru",
) -> Path | None:
    """Attempts to run faster-whisper on clip audio to produce an .ass subtitle file.

    Returns None if faster-whisper is not installed or SUBTITLES is disabled.
    """
    if not settings.subtitles:
        log.info("Subtitles module is disabled (SUBTITLES=false)")
        return None

    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError:
        log.warning("faster-whisper is not installed. Skipping subtitle generation.")
        return None

    try:
        model = WhisperModel("base", device="auto", compute_type="default")
        segments, _ = model.transcribe(str(clip_path), language=language, word_timestamps=True)

        all_words: list[SubtitleWord] = []
        for segment in segments:
            if segment.words:
                for w in segment.words:
                    all_words.append(
                        SubtitleWord(word=w.word.strip(), start_s=w.start, end_s=w.end)
                    )

        if not all_words:
            return None

        return generate_ass_file(all_words, out_ass_path)
    except Exception as exc:
        log.warning("Subtitle transcription failed: %s", exc)
        return None
