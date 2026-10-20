import json
from pathlib import Path
from typing import Any

import pytest
from scripts.calibrate import (
    CalibrationConfig,
    Moment,
    Prepared,
    decide,
    iou,
    load_config,
    main,
    match_moments,
    parse_scan_clips,
    plan_estimate,
    render_report,
    run_experiments,
)
from tests.unit.test_llm import FakeSDK

from cliphunter.config import Settings
from cliphunter.llm.budget import Budget
from cliphunter.llm.client import GeminiVideoClient


def clip(start: str, end: str) -> dict[str, Any]:
    return {
        "start": start, "end": end, "confidence_timestamps": "high", "category": "funny",
        "working_title": "t", "hook_text": None, "visual_hook": "v", "what_happens": "w",
        "visual_evidence": "e", "audio_evidence": "a", "why_viral": "y", "ending_text": None,
        "title_options": ["a"], "score": 8,
    }  # fmt: skip


def resp(*pairs: tuple[str, str]) -> str:
    return json.dumps({"notes": None, "clips": [clip(s, e) for s, e in pairs]})


def test_iou() -> None:
    assert iou((0, 10), (0, 10)) == 1.0
    assert iou((0, 10), (10, 20)) == 0.0
    assert iou((0, 10), (5, 15)) == pytest.approx(5 / 15)


def test_match_moments_recall_and_error() -> None:
    gold = [Moment(100, 130), Moment(300, 340), Moment(500, 520)]
    pred = [(102, 131), (298, 345), (900, 930)]
    st = match_moments(gold, pred)
    assert (st.n_matched, st.n_pred) == (2, 3)
    assert st.recall == pytest.approx(2 / 3)
    assert st.mean_start_err_s == pytest.approx(2.0)  # (2 + 2) / 2
    assert st.mean_end_err_s == pytest.approx(3.0)  # (1 + 5) / 2
    assert st.mean_err_s == pytest.approx(2.5)


def test_match_is_one_to_one() -> None:
    st = match_moments([Moment(0, 10), Moment(0, 10)], [(0, 10)])
    assert st.n_matched == 1


def test_match_empty() -> None:
    st = match_moments([Moment(0, 10)], [])
    assert st.recall == 0.0 and st.mean_err_s is None


def test_parse_scan_clips_global_and_drop() -> None:
    text = resp(("00:10", "00:40"), ("09:50", "12:00"), ("bad", "01:00"))
    out, dropped = parse_scan_clips(text, chunk_len_s=600, offset_s=4200)
    assert out == [(4210.0, 4240.0)]
    assert dropped == 2  # out of chunk + unparseable


def test_load_config(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        "source_url: https://youtu.be/x\nchunk_start_s: 100\n"
        "moments:\n  - {start_s: 110, end_s: 140, note: a}\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.window_start == 100 and cfg.moments[0].end_s == 140
    p.write_text("moments: []\nsource_url: x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(p)


def test_example_config_loads() -> None:
    cfg = load_config(Path("golden/calibration.example.yaml"))
    assert cfg.moments


def test_plan_estimate_is_small_and_offline() -> None:
    s = Settings(_env_file=None)
    cfg = CalibrationConfig(source_url="u", moments=[Moment(1, 2)])
    est = plan_estimate(cfg, s)
    assert sum(e[2] for e in est) < 1.0
    assert any("file high" in e[0] for e in est)


def make_client(tmp: Path, sdk: FakeSDK, limit: float = 10.0) -> tuple[GeminiVideoClient, Settings]:
    s = Settings(_env_file=None, data_dir=tmp, gemini_api_key="k")  # type: ignore[arg-type]
    return GeminiVideoClient(s, budget=Budget(limit), sdk=sdk), s


def test_run_experiments_with_fake_gemini(tmp_path: Path) -> None:
    # Window-relative timecodes: moment at global 4210-4240, window starts at 4200.
    sdk = FakeSDK(resp(("00:10", "00:40")), total_input_tokens=30_000)
    client, s = make_client(tmp_path, sdk)
    cfg = CalibrationConfig(
        source_url="https://youtu.be/x", chunk_start_s=4200, moments=[Moment(4210, 4240)]
    )
    prep = Prepared("files/abc", "video/mp4", "https://youtu.be/x", 5000.0)
    runs = run_experiments(cfg, client, s, prep)

    labels = [r.label for r in runs]
    assert "url+offsets (ts as window-relative)" in labels
    assert "url+offsets (ts as absolute)" in labels
    assert "file agentic (low)" in labels and "thinking=high" in labels
    # no-offsets run skipped because the source is longer than url_full_max_duration_s
    assert any("skipped" in r.note for r in runs)
    rel = next(r for r in runs if r.label.startswith("url+offsets (ts as window"))
    ab = next(r for r in runs if r.label.startswith("url+offsets (ts as abs"))
    assert rel.n_matched == 1 and ab.n_matched == 0
    assert ab.cost_usd == 0.0  # served from cache

    d = decide(runs, cfg)
    assert d["URL_TS_RELATIVE_TO_WINDOW"] is True
    # expected 300 s * 100 tok/s = 30000 observed 30000 => OK
    assert d["URL_OFFSETS_OK"] is True

    offs = next(c for c in sdk.calls if isinstance(c["input"][0].get("processing"), dict))
    assert offs["input"][0]["processing"] == {
        "type": "static",
        "start_offset": 4200,
        "end_offset": 4500,
    }
    agentic = [c for c in sdk.calls if c["input"][0].get("processing") == "agentic"]
    assert len(agentic) == 1


def test_offsets_flagged_when_audio_billed_for_whole_video(tmp_path: Path) -> None:
    sdk = FakeSDK(resp(), total_input_tokens=300_000)  # 10x the expected 30k
    client, s = make_client(tmp_path, sdk)
    cfg = CalibrationConfig(source_url="u", moments=[Moment(1, 2)])
    prep = Prepared("files/a", "video/mp4", "u", None)
    runs = run_experiments(cfg, client, s, prep, only={1})
    assert decide(runs, cfg)["URL_OFFSETS_OK"] is False


def test_budget_stops_live_run(tmp_path: Path) -> None:
    sdk = FakeSDK(resp())
    client, s = make_client(tmp_path, sdk, limit=0.0001)
    cfg = CalibrationConfig(source_file="f.mp4", moments=[Moment(1, 2)])
    runs = run_experiments(cfg, client, s, Prepared("files/a", "video/mp4", None, None), only={3})
    assert sdk.calls == []
    assert all(r.error and r.error.startswith("budget") for r in runs)


def test_report_without_measurements_marks_everything_unmeasured() -> None:
    text = render_report([], {}, {})
    assert text.count("не измерено") == 7
    assert "не принято" in text


def test_report_with_runs(tmp_path: Path) -> None:
    sdk = FakeSDK(resp(("00:10", "00:40")))
    client, s = make_client(tmp_path, sdk)
    cfg = CalibrationConfig(source_file="f.mp4", moments=[Moment(10, 40)])
    runs = run_experiments(cfg, client, s, Prepared("files/a", "video/mp4", None, None), {3})
    text = render_report(runs, decide(runs, cfg), {"date": "2026-01-01", "model": "m",
                                                    "total_cost": 0.1})  # fmt: skip
    assert "file low" in text and "0.1000 USD" in text


def test_dry_run_never_touches_api(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfgp = tmp_path / "c.yaml"
    cfgp.write_text("source_file: x.mp4\nmoments:\n  - {start_s: 1, end_s: 20}\n", encoding="utf-8")
    assert main(["--config", str(cfgp)]) == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "TOTAL" in out


def test_live_without_key_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cliphunter.config import get_settings

    monkeypatch.setenv("GEMINI_API_KEY", "")
    get_settings.cache_clear()
    cfgp = tmp_path / "c.yaml"
    cfgp.write_text("source_file: x.mp4\nmoments:\n  - {start_s: 1, end_s: 20}\n", encoding="utf-8")
    assert main(["--config", str(cfgp), "--live"]) == 2
    get_settings.cache_clear()
