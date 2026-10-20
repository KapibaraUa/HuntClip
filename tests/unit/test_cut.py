import shutil
from pathlib import Path

import pytest

from cliphunter.config import Settings
from cliphunter.models import Candidate, CandidateStatus
from cliphunter.video.cut import cut_candidate, cut_command, verify_rendered_clip
from cliphunter.video.ffmpeg import FFmpegError, run

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_cut_command_structure() -> None:
    cmd = cut_command(Path("in.mp4"), Path("out.mp4"), start_s=15.5, dur_s=25.0, use_nvenc=False)
    s = " ".join(cmd)
    assert "-ss 15.500" in s
    assert "-t 25.000" in s
    assert "afade=t=in:st=0:d=0.1" in s
    assert "afade=t=out:st=24.850:d=0.15" in s
    assert "-c:v libx264" in s
    assert "-crf 18" in s
    assert "-movflags +faststart" in s


def test_cut_candidate_execution(tmp_path: Path) -> None:
    # Generate 10-second synthetic test video
    src = tmp_path / "src.mp4"
    run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=24:duration=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(src),
        ],
        timeout_s=30,
    )

    cand = Candidate(
        id="test_cand_1",
        start_s=2.0,
        end_s=5.0,
        category="funny",
        working_title="hilarious_moment",
        score=9,
        status=CandidateStatus.RAW,
    )

    s = Settings(_env_file=None)
    out_file = cut_candidate(cand, src, tmp_path / "out", idx=1, settings=s)
    assert out_file.exists()
    assert out_file.name == "01_hilarious_moment.mp4"

    info = verify_rendered_clip(out_file, expected_dur_s=3.0)
    assert info["has_video"] is True
    assert info["has_audio"] is True
    assert info["actual_duration_s"] == pytest.approx(3.0, abs=0.2)


def test_verify_rendered_clip_missing() -> None:
    with pytest.raises(FFmpegError):
        verify_rendered_clip(Path("does_not_exist.mp4"), 10.0)
