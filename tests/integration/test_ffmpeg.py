import shutil
from pathlib import Path

import pytest

from cliphunter.video.ffmpeg import probe_duration_s, proxy_cmd, run

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_proxy_window_is_accurate(tmp_path: Path) -> None:
    src = tmp_path / "src.mp4"
    run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=20",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src),
        ],
        60,
    )  # fmt: skip
    out = tmp_path / "out.mp4"
    run(proxy_cmd(src, out, start_s=5, dur_s=7), 60)
    assert probe_duration_s(out) == pytest.approx(7.0, abs=0.15)
