import json
import logging

from cliphunter.config import Settings
from cliphunter.logging import JsonFormatter, mask, set_job_id


def test_defaults_match_plan() -> None:
    s = Settings(_env_file=None)
    assert s.gemini_model == "gemini-3.8-flash"
    assert (s.chunk_s, s.overlap_s, s.short_video_max_s) == (2700, 120, 2700)
    assert s.agentic_mode is False
    assert s.judge_batch == 10
    assert s.llm_concurrency == 2


def test_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CHUNK_S", "1200")
    monkeypatch.setenv("TELEGRAM_ALLOWED_IDS", "1, 2;3")
    s = Settings(_env_file=None)
    assert s.chunk_s == 1200
    assert s.telegram_allowed_ids == [1, 2, 3]


def test_overlap_must_be_smaller_than_chunk() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, chunk_s=100, overlap_s=100)


def test_chunk_fits_context() -> None:
    s = Settings(_env_file=None)
    assert s.chunk_fits_context("low")  # 2700*100 = 270k < 600k
    assert not s.chunk_fits_context("high")  # 2700*300 = 810k > 600k


def test_secret_not_in_repr() -> None:
    s = Settings(_env_file=None, gemini_api_key="AIzaSyDUMMYDUMMYDUMMYDUMMYDUMMY12345")  # type: ignore[arg-type]
    assert "AIza" not in repr(s)
    assert s.secrets() == ["AIzaSyDUMMYDUMMYDUMMYDUMMYDUMMY12345"]


def test_env_example_covers_plan_section_12() -> None:
    from pathlib import Path

    text = Path(".env.example").read_text(encoding="utf-8")
    required = [
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "AGENTIC_MODE",
        "MEDIA_RES_SCAN",
        "MEDIA_RES_REFINE",
        "REFINE_FPS",
        "CHUNK_S",
        "OVERLAP_S",
        "SHORT_VIDEO_MAX_S",
        "PAD_BEFORE_S",
        "PAD_AFTER_S",
        "SNAP_S",
        "CLIP_MIN_S",
        "CLIP_MAX_S",
        "MAX_CANDIDATES_PER_HOUR",
        "MAX_PER_30MIN",
        "JUDGE_BATCH",
        "LLM_CONCURRENCY",
        "MAX_COST_USD_PER_JOB",
        "CONFIRM_ABOVE_USD",
        "PRICE_IN_PER_M",
        "PRICE_OUT_PER_M",
        "URL_OFFSETS_OK",
        "URL_TS_RELATIVE_TO_WINDOW",
        "AUTO_RENDER",
        "SUBTITLES",
        "CAPCUT_EXPORT",
        "USE_NVENC",
        "FFMPEG_TIMEOUT_S",
        "DATA_DIR",
        "RETENTION_DAYS",
        "DATABASE_URL",
        "REDIS_URL",
        "TELEGRAM_TOKEN",
        "TELEGRAM_ALLOWED_IDS",
    ]
    keys = {ln.split("=")[0] for ln in text.splitlines() if "=" in ln and not ln.startswith("#")}
    assert set(required) <= keys
    # Every key in .env.example must be a known Settings field.
    fields = {n.upper() for n in Settings.model_fields}
    assert keys <= fields


def test_mask_patterns() -> None:
    assert "AIzaSyABCDEFGHIJKLMNOPQRSTUV" not in mask("key AIzaSyABCDEFGHIJKLMNOPQRSTUV x")
    assert "secret123" not in mask("https://x/?key=secret123&a=1")
    assert "supersecret" not in mask("val supersecret here", ["supersecret"])
    assert "123456789:ABCdefGHIjklMNOpqrsTUVwxyz0123456789" not in mask(
        "bot 123456789:ABCdefGHIjklMNOpqrsTUVwxyz0123456789"
    )


def test_json_formatter_has_job_id_and_masks() -> None:
    fmt = JsonFormatter(["topsecret"])
    token = set_job_id("job-42")
    try:
        rec = logging.LogRecord("t", logging.INFO, "", 0, "hello topsecret", (), None)
        rec.cost = 0.5
        out = json.loads(fmt.format(rec))
    finally:
        from cliphunter.logging import job_id_var

        job_id_var.reset(token)
    assert out["job_id"] == "job-42"
    assert "topsecret" not in json.dumps(out)
    assert out["cost"] == 0.5
