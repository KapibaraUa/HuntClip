import subprocess
from pathlib import Path
from unittest.mock import patch

from cliphunter.video.snap import (
    detect_scene_cuts,
    detect_silence_points,
    snap_boundary,
    snap_candidate_boundaries,
)


def test_snap_boundary_empty() -> None:
    assert snap_boundary(10.0, [], max_snap_s=1.0) == 10.0


def test_snap_boundary_closest_within_range() -> None:
    # Target 10.0, candidates at 9.2 and 10.3 -> 10.3 is dist 0.3, 9.2 is dist 0.8
    pts = [9.2, 10.3, 11.5]
    assert snap_boundary(10.0, pts, max_snap_s=1.0) == 10.3


def test_snap_boundary_out_of_range() -> None:
    # Target 10.0, closest is 11.2 (dist 1.2 > 1.0)
    pts = [8.0, 11.2]
    assert snap_boundary(10.0, pts, max_snap_s=1.0) == 10.0


def test_detect_silence_points_parsing() -> None:
    sample_stderr = """
[silencedetect @ 0000021] silence_start: 1.500
[silencedetect @ 0000021] silence_end: 2.100 | silence_duration: 0.600
[silencedetect @ 0000021] silence_start: 5.250
    """
    mock_proc = subprocess.CompletedProcess(
        args=["ffmpeg"], returncode=0, stdout="", stderr=sample_stderr
    )

    with patch("cliphunter.video.snap.run", return_value=mock_proc):
        # start_s = 10.0, so base_t = 10.0
        pts = detect_silence_points(Path("dummy.mp4"), start_s=10.0, dur_s=5.0)

    assert pts == [11.5, 12.1, 15.25]


def test_detect_scene_cuts_parsing() -> None:
    sample_stderr = """
frame:0    pts:0       pts_time:0.0
frame:24   pts:24000   pts_time:1.0
frame:48   pts:48000   pts_time:2.5
    """
    mock_proc = subprocess.CompletedProcess(
        args=["ffmpeg"], returncode=0, stdout="", stderr=sample_stderr
    )

    with patch("cliphunter.video.snap.run", return_value=mock_proc):
        cuts = detect_scene_cuts(Path("dummy.mp4"), start_s=20.0, dur_s=5.0)

    assert cuts == [20.0, 21.0, 22.5]


def test_snap_candidate_boundaries_snaps_both() -> None:
    with patch("cliphunter.video.snap.detect_silence_points") as mock_silence:
        # For start (around 10.0), return [9.8, 10.5] -> snaps to 9.8
        # For end (around 25.0), return [24.7, 25.4] -> snaps to 25.4 (closer is 25.4 vs 24.7)
        mock_silence.side_effect = [
            [9.8, 10.5],
            [24.7, 25.2],
        ]
        s, e = snap_candidate_boundaries(10.0, 25.0, Path("dummy.mp4"), max_snap_s=1.0)

    assert s == 9.8
    assert e == 25.2


def test_snap_candidate_boundaries_rejects_negative_or_too_short() -> None:
    with patch("cliphunter.video.snap.detect_silence_points") as mock_silence:
        # Snapped interval < 1.0s -> reverts to original
        mock_silence.side_effect = [
            [10.8],
            [10.9],
        ]
        s, e = snap_candidate_boundaries(10.0, 11.5, Path("dummy.mp4"), max_snap_s=1.0)

    assert s == 10.0
    assert e == 11.5
