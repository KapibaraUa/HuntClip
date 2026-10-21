"""Integration tests rendering a candidate moment in all Phase 4 layouts (plan, Section 14).

Verifies via FFmpeg and ffprobe:
- original (16:9)
- center_crop_9x16 (1080x1920)
- blur_fit_9x16 (1080x1920)
- facecam_top_game_bottom_9x16 (1080x1920)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cliphunter.config import Settings
from cliphunter.models import Candidate, Profile
from cliphunter.video.ffmpeg import run
from cliphunter.video.render import render_candidate, verify_rendered_clip_dimensions


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generates a synthetic 4-second 1920x1080 video with video and audio tracks."""
    base_dir = tmp_path_factory.mktemp("video_source")
    sample_path = base_dir / "synthetic_stream.mp4"

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=4:size=1920x1080:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=4",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(sample_path),
    ]
    run(cmd, timeout_s=30)
    assert sample_path.is_file() and sample_path.stat().st_size > 0
    return sample_path


def test_render_all_layouts_and_verify_ffprobe(sample_video: Path, tmp_path: Path) -> None:
    """Tests Phase 4 acceptance criteria: one moment rendered in all layouts with ffprobe validation."""
    cand = Candidate(
        id="cand_test_p4",
        start_s=0.5,
        end_s=2.5,  # 2.0s duration
        category="game_moment",
        working_title="Epic Reaction Moment",
        score=9,
    )

    profile = Profile(
        name="test_streamer",
        facecam_rect={"x": 0.70, "y": 0.05, "w": 0.28, "h": 0.35},
        gameplay_rect={"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
    )

    settings = Settings()
    layouts_to_test = [
        "original",
        "center_crop_9x16",
        "blur_fit_9x16",
        "facecam_top_game_bottom_9x16",
    ]

    rendered_files: list[Path] = []

    for idx, layout in enumerate(layouts_to_test, start=1):
        out_clip = render_candidate(
            candidate=cand,
            src=sample_video,
            out_dir=tmp_path / "clips",
            idx=idx,
            layout=layout,
            profile=profile,
            settings=settings,
            title="Epic Clutch",
        )
        assert out_clip.is_file()
        rendered_files.append(out_clip)

        # Validate with ffprobe
        stats = verify_rendered_clip_dimensions(
            out_clip,
            expected_dur_s=cand.duration_s,
            layout=layout,
            tolerance_s=0.25,
        )

        assert stats["size_bytes"] > 0
        assert abs(stats["actual_duration_s"] - cand.duration_s) <= 0.25

        if "9x16" in layout:
            assert stats["width"] == 1080
            assert stats["height"] == 1920
        else:
            assert stats["width"] == 1920
            assert stats["height"] == 1080

    assert len(rendered_files) == 4
