from pathlib import Path

from cliphunter.ingest.youtube import section_cmd
from cliphunter.video.ffmpeg import probe_cmd, proxy_cmd


def test_proxy_cmd_matches_plan_s1() -> None:
    cmd = proxy_cmd(Path("in.mp4"), Path("out.mp4"), start_s=600, dur_s=300)
    s = " ".join(cmd)
    assert cmd.index("-ss") < cmd.index("-i")  # seek before input
    assert "scale=-2:720" in s and "-r 24" in s and "-crf 30" in s and "-b:a 96k" in s
    assert "-t 300.000" in s and cmd[-1] == "out.mp4"


def test_proxy_cmd_whole_file_has_no_seek() -> None:
    cmd = proxy_cmd(Path("in.mp4"), Path("out.mp4"))
    assert "-ss" not in cmd and "-t" not in cmd


def test_probe_cmd() -> None:
    assert probe_cmd(Path("a.mp4"))[0] == "ffprobe"


def test_ytdlp_section_cmd() -> None:
    cmd = section_cmd("https://youtu.be/x", 100, 700, Path("o.mp4"))
    assert "--download-sections" in cmd and "*100-700" in cmd
    assert "--force-keyframes-at-cuts" in cmd
