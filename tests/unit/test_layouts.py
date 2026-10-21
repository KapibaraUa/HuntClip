"""Unit tests for layouts, template overlays, audio loudnorm, and ASS subtitles."""

from __future__ import annotations

from pathlib import Path

from cliphunter.models import Profile
from cliphunter.video.layouts import (
    build_blur_fit_9x16_filter,
    build_center_crop_9x16_filter,
    build_original_filter,
    build_split_facecam_game_filter,
    get_layout_filter,
)
from cliphunter.video.render import build_audio_filter, build_render_command
from cliphunter.video.subtitles import SubtitleWord, format_ass_timestamp, generate_ass_file
from cliphunter.video.template import build_title_hook_filter, escape_drawtext


def test_layout_filter_strings() -> None:
    # 1. Original (16:9)
    assert build_original_filter() == ""
    assert get_layout_filter("original") == ""

    # 2. Center crop (9:16)
    crop_filt = build_center_crop_9x16_filter(1080, 1920)
    assert "crop=ih*9/16:ih" in crop_filt
    assert "scale=1080:1920" in crop_filt
    assert get_layout_filter("center_crop_9x16") == crop_filt

    # 3. Blur fit (9:16)
    blur_filt = build_blur_fit_9x16_filter(1080, 1920)
    assert "boxblur=30:5" in blur_filt
    assert "overlay=(W-w)/2:(H-h)/2" in blur_filt
    assert get_layout_filter("blur_fit_9x16") == blur_filt

    # 4. Facecam + Gameplay split (9:16)
    profile = Profile(
        name="streamer",
        facecam_rect={"x": 0.75, "y": 0.05, "w": 0.25, "h": 0.30},
        gameplay_rect={"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
    )
    split_filt = build_split_facecam_game_filter(profile=profile, target_w=1080, target_h=1920)
    assert "vstack=inputs=2" in split_filt
    assert "0.750*iw" in split_filt
    assert get_layout_filter("facecam_top_game_bottom_9x16", profile=profile) == split_filt


def test_title_hook_escaping_and_template() -> None:
    escaped = escape_drawtext("Title: 'Clutch' & 100%")
    assert "\\:" in escaped
    assert "'\\''" in escaped
    assert "\\%" in escaped

    filt = build_title_hook_filter("Best Moment", start_t=0.2, end_t=3.5)
    assert "drawtext" in filt
    assert "Best Moment" in filt
    assert "between(t,0.20,3.50)" in filt


def test_audio_loudnorm_and_fades() -> None:
    filt_norm = build_audio_filter(dur_s=30.0, normalize_loudness=True)
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in filt_norm
    assert "afade=t=in:st=0:d=0.1" in filt_norm
    assert "afade=t=out:st=29.850:d=0.15" in filt_norm

    filt_no_norm = build_audio_filter(dur_s=10.0, normalize_loudness=False)
    assert "loudnorm" not in filt_no_norm
    assert "afade" in filt_no_norm


def test_ass_subtitles_generation(tmp_path: Path) -> None:
    words = [
        SubtitleWord(word="Привет", start_s=0.5, end_s=1.0),
        SubtitleWord(word="всем", start_s=1.0, end_s=1.4),
        SubtitleWord(word="зрителям", start_s=1.4, end_s=2.0),
    ]
    ass_path = tmp_path / "subs.ass"
    out = generate_ass_file(words, ass_path)
    assert out.is_file()
    content = out.read_text(encoding="utf-8")

    assert "[Script Info]" in content
    assert "[V4+ Styles]" in content
    assert "Dialogue:" in content
    assert format_ass_timestamp(65.5) == "0:01:05.50"


def test_build_render_command() -> None:
    cmd = build_render_command(
        src=Path("input.mp4"),
        dst=Path("output.mp4"),
        start_s=10.0,
        dur_s=25.0,
        layout="blur_fit_9x16",
        title="Epic Clutch",
    )
    cmd_str = " ".join(cmd)
    assert "ffmpeg" in cmd_str
    assert "-filter_complex" in cmd_str
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in cmd_str
    assert "output.mp4" in cmd_str
