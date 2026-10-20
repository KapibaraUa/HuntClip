"""Calibration (plan section 8, items 1-7). Writes docs/CALIBRATION.md.

SAFE BY DEFAULT: without --live nothing is sent to the API. The script only prints the plan and a
cost estimate. A live run additionally requires GEMINI_API_KEY and is capped by
CALIBRATION_MAX_COST_USD (the budget guard stops the run before any call that would exceed it).

Usage:
  python scripts/calibrate.py --config calibration/calibration.yaml            # dry run
  python scripts/calibrate.py --config calibration/calibration.yaml --live     # spends money
  python scripts/calibrate.py --render-only                                    # rebuild report
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from cliphunter.config import MediaRes, Settings, get_settings
from cliphunter.llm.budget import (
    Budget,
    BudgetExceeded,
    estimate_video_cost,
    estimate_video_tokens,
)
from cliphunter.llm.client import CallResult, GeminiVideoClient
from cliphunter.llm.prompts import load_prompt, scan_user_message
from cliphunter.llm.schemas import ScanResponse
from cliphunter.logging import setup_logging
from cliphunter.pipeline.timecodes import format_ts, parse_ts, to_global, within_chunk

log = logging.getLogger("calibrate")
ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "docs" / "CALIBRATION.md"
RESULTS_PATH = ROOT / "calibration" / "results.json"
IOU_MATCH = 0.5


# --- metrics (pure, unit-tested) ------------------------------------------------------------
@dataclass
class Moment:
    start_s: float
    end_s: float
    note: str = ""


def iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


@dataclass
class MatchStats:
    n_gold: int
    n_pred: int
    n_matched: int
    recall: float
    mean_start_err_s: float | None
    mean_end_err_s: float | None
    mean_err_s: float | None


def match_moments(
    gold: list[Moment], pred: list[tuple[float, float]], threshold: float = IOU_MATCH
) -> MatchStats:
    """Greedy one-to-one matching by IoU; boundary error is measured on matched pairs only."""
    pairs = sorted(
        (
            (iou((g.start_s, g.end_s), p), gi, pi)
            for gi, g in enumerate(gold)
            for pi, p in enumerate(pred)
        ),
        reverse=True,
    )
    used_g: set[int] = set()
    used_p: set[int] = set()
    start_errs: list[float] = []
    end_errs: list[float] = []
    for score, gi, pi in pairs:
        if score < threshold:
            break
        if gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        start_errs.append(abs(gold[gi].start_s - pred[pi][0]))
        end_errs.append(abs(gold[gi].end_s - pred[pi][1]))

    def mean(xs: list[float]) -> float | None:
        return sum(xs) / len(xs) if xs else None

    return MatchStats(
        n_gold=len(gold),
        n_pred=len(pred),
        n_matched=len(used_g),
        recall=len(used_g) / len(gold) if gold else 0.0,
        mean_start_err_s=mean(start_errs),
        mean_end_err_s=mean(end_errs),
        mean_err_s=mean(start_errs + end_errs),
    )


def parse_scan_clips(
    text: str, chunk_len_s: float, offset_s: float
) -> tuple[list[tuple[float, float]], int]:
    """Parse a ScanResponse into global (start, end) pairs. Returns (clips, dropped_count)."""
    resp = ScanResponse.model_validate_json(text)
    out: list[tuple[float, float]] = []
    dropped = 0
    for c in resp.clips:
        try:
            s, e = parse_ts(c.start), parse_ts(c.end)
        except ValueError:
            dropped += 1
            continue
        if not within_chunk(s, e, chunk_len_s):
            dropped += 1
            continue
        out.append((to_global(s, offset_s), to_global(e, offset_s)))
    return out, dropped


# --- config ---------------------------------------------------------------------------------
@dataclass
class CalibrationConfig:
    channel: str = "evelone2004"
    game: str = ""
    language: str = "ru"
    source_url: str | None = None
    source_file: str | None = None
    chunk_start_s: float = 0.0
    chunk_len_s: float = 600.0
    url_window_start_s: float | None = None  # defaults to chunk_start_s
    url_window_len_s: float = 300.0
    url_full_max_duration_s: float = 900.0  # run "no offsets" only if the whole video is shorter
    offsets_ratio_max: float = 1.5  # observed/expected input tokens above this = offsets not OK
    max_candidates: int = 8
    thinking_levels: list[str] = field(default_factory=lambda: ["low", "medium", "high"])
    moments: list[Moment] = field(default_factory=list)

    @property
    def window_start(self) -> float:
        return self.chunk_start_s if self.url_window_start_s is None else self.url_window_start_s


def load_config(path: Path) -> CalibrationConfig:
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    moments = [Moment(**m) for m in raw.pop("moments", [])]
    cfg = CalibrationConfig(**raw, moments=moments)
    if not cfg.source_url and not cfg.source_file:
        raise ValueError("calibration config needs source_url or source_file")
    if not cfg.moments:
        raise ValueError("calibration config needs at least one entry in 'moments'")
    return cfg


# --- experiments ----------------------------------------------------------------------------
@dataclass
class Prepared:
    """Inputs for live runs. Built by prepare() or directly in tests."""

    chunk_uri: str
    chunk_mime: str
    url: str | None
    source_duration_s: float | None


@dataclass
class RunRecord:
    experiment: str
    label: str
    mode: str
    resolution: str
    thinking: str
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thought: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    n_pred: int = 0
    n_dropped: int = 0
    n_matched: int = 0
    recall: float | None = None
    mean_start_err_s: float | None = None
    mean_end_err_s: float | None = None
    mean_err_s: float | None = None
    expected_tokens_in: int | None = None
    error: str | None = None
    note: str = ""


def _system_and_user(cfg: CalibrationConfig, settings: Settings) -> tuple[str, str, int]:
    p = load_prompt("scan")
    user = scan_user_message(
        channel=cfg.channel,
        game=cfg.game or "не указана",
        language=cfg.language,
        clip_min_s=settings.clip_min_s,
        clip_max_s=settings.clip_max_s,
        max_candidates=cfg.max_candidates,
    )
    return p.body, user, p.version


def _execute(
    client: GeminiVideoClient,
    cfg: CalibrationConfig,
    settings: Settings,
    *,
    experiment: str,
    label: str,
    mode: str,
    video: dict[str, Any],
    resolution: MediaRes,
    thinking: str,
    duration_s: float,
    offset_s: float,
    gold: list[Moment],
    extra_user: str = "",
    expected_tokens: int | None = None,
) -> RunRecord:
    system, user, version = _system_and_user(cfg, settings)
    rec = RunRecord(
        experiment, label, mode, resolution, thinking, expected_tokens_in=expected_tokens
    )
    est = estimate_video_cost(duration_s, resolution, settings)
    try:
        res: CallResult = client.call(
            stage=f"calibrate:{experiment}",
            mode=mode,
            video=video,
            prompt=user + extra_user,
            system=system,
            schema=ScanResponse,
            thinking_level=thinking,
            estimated_cost_usd=est,
            cache_parts={"prompt_version": version},
        )
    except BudgetExceeded as exc:
        rec.error = f"budget: {exc}"
        return rec
    except Exception as exc:
        rec.error = repr(exc)
        return rec
    rec.tokens_in, rec.tokens_out = res.usage.billed_in, res.usage.tokens_out
    rec.tokens_thought, rec.cost_usd = res.usage.tokens_thought, res.cost_usd
    rec.latency_ms = res.latency_ms
    try:
        preds, dropped = parse_scan_clips(res.text, duration_s, offset_s)
    except (ValidationError, ValueError) as exc:
        rec.error = f"unparseable response: {exc}"
        return rec
    local_gold = [g for g in gold if g.end_s > offset_s and g.start_s < offset_s + duration_s]
    stats = match_moments(local_gold, preds)
    rec.n_pred, rec.n_dropped, rec.n_matched = stats.n_pred, dropped, stats.n_matched
    rec.recall = stats.recall if local_gold else None
    rec.mean_start_err_s, rec.mean_end_err_s = stats.mean_start_err_s, stats.mean_end_err_s
    rec.mean_err_s = stats.mean_err_s
    return rec


def run_experiments(
    cfg: CalibrationConfig,
    client: GeminiVideoClient,
    settings: Settings,
    prep: Prepared,
    only: set[int] | None = None,
) -> list[RunRecord]:
    def want(n: int) -> bool:
        return only is None or n in only

    runs: list[RunRecord] = []
    chunk_video: dict[str, Any] = {
        "type": "video",
        "uri": prep.chunk_uri,
        "mime_type": prep.chunk_mime,
    }
    ws, wl = cfg.window_start, cfg.url_window_len_s
    res_scan = settings.media_res_scan

    def file_run(
        exp: str, label: str, res: MediaRes, thinking: str, processing: str | None = None
    ) -> RunRecord:
        video: dict[str, Any] = {**chunk_video, "resolution": res}
        if processing:
            video["processing"] = processing
        return _execute(
            client,
            cfg,
            settings,
            experiment=exp,
            label=label,
            mode="file_agentic" if processing else "file",
            video=video,
            resolution=res,
            thinking=thinking,
            duration_s=cfg.chunk_len_s,
            offset_s=cfg.chunk_start_s,
            gold=cfg.moments,
            expected_tokens=estimate_video_tokens(cfg.chunk_len_s, res, settings),
        )

    # 1-2. url-mode: offsets vs none; timecode reference frame
    if (want(1) or want(2)) and prep.url:
        video: dict[str, Any] = {
            "type": "video",
            "uri": prep.url,
            "resolution": res_scan,
            "processing": {"type": "static", "start_offset": ws, "end_offset": ws + wl},
        }
        # Gold is compared under both hypotheses: window-relative and absolute timecodes.
        rel = _execute(
            client,
            cfg,
            settings,
            experiment="1",
            label="url+offsets (ts as window-relative)",
            mode="url_offsets",
            video=video,
            resolution=res_scan,
            thinking=settings.thinking_level,
            duration_s=wl,
            offset_s=ws,
            gold=cfg.moments,
            expected_tokens=estimate_video_tokens(wl, res_scan, settings),
        )
        runs.append(rel)
        if rel.error is None:
            # Same cached response re-read with the other hypothesis (cache hit, costs nothing).
            ab = _execute(
                client,
                cfg,
                settings,
                experiment="2",
                label="url+offsets (ts as absolute)",
                mode="url_offsets",
                video=video,
                resolution=res_scan,
                thinking=settings.thinking_level,
                duration_s=ws + wl,
                offset_s=0.0,
                gold=cfg.moments,
            )
            ab.note = "re-parse of the same response under the 'absolute' hypothesis"
            runs.append(ab)
        full_dur = prep.source_duration_s
        if full_dur is not None and full_dur <= cfg.url_full_max_duration_s:
            full: dict[str, Any] = {"type": "video", "uri": prep.url, "resolution": res_scan}
            extra = f"\nАнализируй только фрагмент с {format_ts(ws)} по {format_ts(ws + wl)}."
            runs.append(
                _execute(
                    client,
                    cfg,
                    settings,
                    experiment="1",
                    label="url, no offsets (window in prompt)",
                    mode="url",
                    video=full,
                    resolution=res_scan,
                    thinking=settings.thinking_level,
                    duration_s=full_dur,
                    offset_s=0.0,
                    gold=cfg.moments,
                    extra_user=extra,
                    expected_tokens=estimate_video_tokens(full_dur, res_scan, settings),
                )
            )
        else:
            runs.append(
                RunRecord(
                    "1",
                    "url, no offsets",
                    "url",
                    res_scan,
                    settings.thinking_level,
                    note="skipped: whole video longer than url_full_max_duration_s",
                )
            )

    # 3-4. file-mode low vs high; 7 (timestamp error) comes from the same runs
    if want(3) or want(4) or want(7):
        runs.append(file_run("3-4", "file low", "low", settings.thinking_level))
        runs.append(file_run("3-4", "file high", "high", settings.thinking_level))

    # 5. static vs agentic
    if want(5):
        runs.append(file_run("5", "file agentic (low)", "low", settings.thinking_level, "agentic"))

    # 6. thinking levels
    if want(6):
        for lvl in cfg.thinking_levels:
            runs.append(file_run("6", f"thinking={lvl}", "low", lvl))
    return runs


def decide(runs: list[RunRecord], cfg: CalibrationConfig) -> dict[str, Any]:
    """Derive the values written to .env.example. None means 'not measured / inconclusive'."""
    out: dict[str, Any] = {"URL_OFFSETS_OK": None, "URL_TS_RELATIVE_TO_WINDOW": None}
    off = next((r for r in runs if r.mode == "url_offsets" and r.experiment == "1"), None)
    if off and off.error is None and off.expected_tokens_in:
        ratio = off.tokens_in / off.expected_tokens_in
        out["url_offsets_ratio"] = round(ratio, 2)
        out["URL_OFFSETS_OK"] = ratio <= cfg.offsets_ratio_max
    ab = next((r for r in runs if r.experiment == "2"), None)
    if off and ab and off.error is None and ab.error is None and off.n_matched != ab.n_matched:
        out["URL_TS_RELATIVE_TO_WINDOW"] = off.n_matched > ab.n_matched
    return out


# --- report ---------------------------------------------------------------------------------
FACTS_VERIFIED = """\
Сверено с ai.google.dev 2026-10-20 (страницы video-understanding, media-resolution, thinking,
structured-output, tokens, pricing). Это сверка документации, не измерение.

| Пункт плана | Что в документации |
| --- | --- |
| Режим API и SDK | Interactions API: `client.interactions.create(model, input, ...)`, SDK `google-genai` 2.31.0. Ответ: `interaction.output_text`. |
| Идентификатор модели | `gemini-3.8-flash` присутствует в документации (владелец подтвердил). |
| Видео-часть запроса | `{"type":"video","uri":...,"mime_type":...,"processing":...,"resolution":...}`; текст-промпт ставится после видео. |
| YouTube по ссылке | Видео-часть только с `uri` ссылки. |
| Offsets | `processing: {"type":"static","start_offset":сек,"end_offset":сек}`. Работают только в static-режиме. |
| fps | `processing: {"type":"static","fps":0.5}`. Только static. По умолчанию 1 кадр/с. |
| Агентный режим | `processing: "agentic"`; поддерживается 3.8 Flash. Модель сама подгружает **транскрипт**, кадры и аудио. Расход считается как thought и tool-use токены. По плану выключен (`AGENTIC_MODE=false`). |
| media_resolution | Уровни: `unspecified`, `low`, `medium`, `high`; задаётся на каждую часть полем `resolution` (только Gemini 3). |
| Токены видео (static) | Кадр: 66 ток. при `low`, иначе 258; аудио 32 ток./с. Итого около 100 ток./с (low) и около 300 ток./с (high). |
| Контекст | 1M токенов: до 3 часов при low, до 1 часа при high. |
| Мышление | `generation_config: {"thinking_level": ...}`. Для 3.8 Flash: low, medium, high; по умолчанию medium. |
| Структурированный ответ | `response_format: {"type":"text","mime_type":"application/json","schema": <JSON schema>}`. |
| Учёт токенов | `interaction.usage`: `total_input_tokens`, `total_output_tokens`, `total_thought_tokens`, `total_tool_use_tokens`, `total_cached_tokens`, `total_tokens`. |
| Цены, Standard, за 1M | Вход 0.75 USD, выход (включая мышление) 3.75 USD до 2026-12-31. С 2027-01-01: 1.50 и 7.50. Batch и Flex вдвое дешевле. |
| Files API | Загрузка `client.files.upload(file=...)`, опрос `files.get`, пока состояние `PROCESSING`. |

**Не сверено (отметка «сверить» осталась):** допустимые значения safety settings, лимиты RPM/TPM
для вашего тарифа, срок хранения файлов в Files API, максимум видео в одном запросе
(`JUDGE_BATCH` ограничен 10 по плану), лимит Bot API Telegram.

Следствие для плана: на разрешении `high` кусок 45 минут даёт около 810k токенов (81% контекста),
что выше порога 60% из S2. Для прохода 1 это неважно (используется `low`, около 270k токенов).
"""

UNMEASURED_ITEMS = [
    (1, "Токены и стоимость url-mode с offsets и без, окно 5 минут"),
    (2, "Относительно чего возвращаются таймкоды в url-mode"),
    (3, "Токены file-mode на куске 10 минут при low и обычном разрешении"),
    (4, "Качество low и обычного разрешения на известных моментах"),
    (5, "Статический и агентный режимы на одном куске"),
    (6, "Влияние уровня мышления на качество и цену"),
    (7, "Точность таймкодов (средняя ошибка в секундах)"),
]

TABLE_HEADER = (
    "| Пункт | Запуск | Режим | Разр. | Мышл. | Токены вх. | Токены вых. | Мышление | "
    "Стоимость USD | Время мс | Кандидатов | Совпало | Recall | Ошибка нач. с | "
    "Ошибка кон. с | Ошибка ср. с | Примечание |"
)


def _fmt(x: float | None, nd: int = 2) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _decision_text(decisions: dict[str, Any]) -> str:
    ok = decisions.get("URL_OFFSETS_OK")
    if ok is None:
        return (
            "**не принято**, нет измерений. До калибровки используется file-mode "
            "(`URL_OFFSETS_OK=false`)."
        )
    if ok:
        return "включать (`URL_OFFSETS_OK=true`)."
    return "не включать, использовать file-mode (`URL_OFFSETS_OK=false`)."


def render_report(runs: list[RunRecord], decisions: dict[str, Any], meta: dict[str, Any]) -> str:
    lines = ["# CALIBRATION", ""]
    if not runs:
        lines += [
            "> **Статус: измерений пока нет.** Калибровочный прогон не выполнялся (нет стрима и "
            "эталонных моментов, платные вызовы запрещены). Таблицы ниже заполнит "
            "`python scripts/calibrate.py --config calibration/calibration.yaml --live`.",
            "",
        ]
    else:
        lines += [
            f"Прогон: {meta.get('date')}, модель `{meta.get('model')}`, "
            f"итоговая стоимость прогона: **{float(meta.get('total_cost', 0)):.4f} USD**.",
            "",
        ]
    lines += ["## 0. Сверка документации", "", FACTS_VERIFIED, "## 1-7. Измерения", ""]
    if runs:
        lines += [TABLE_HEADER, "| --- " * 17 + "|"]
        for r in runs:
            lines.append(
                f"| {r.experiment} | {r.label} | {r.mode} | {r.resolution} | {r.thinking} | "
                f"{r.tokens_in} | {r.tokens_out} | {r.tokens_thought} | {r.cost_usd:.4f} | "
                f"{r.latency_ms} | {r.n_pred} | {r.n_matched} | {_fmt(r.recall)} | "
                f"{_fmt(r.mean_start_err_s)} | {_fmt(r.mean_end_err_s)} | {_fmt(r.mean_err_s)} | "
                f"{r.error or r.note} |"
            )
        lines.append("")
    else:
        lines += [f"{n}. {title}: **не измерено**" for n, title in UNMEASURED_ITEMS]
        lines.append("")
    lines += ["## Решения", "", "| Параметр | Значение по итогам калибровки |", "| --- | --- |"]
    for k in ("URL_OFFSETS_OK", "URL_TS_RELATIVE_TO_WINDOW"):
        v = decisions.get(k)
        shown = (
            "не определено (в `.env.example` значение по умолчанию)"
            if v is None
            else str(v).lower()
        )
        lines.append(f"| `{k}` | {shown} |")
    if "url_offsets_ratio" in decisions:
        lines.append(
            "| наблюдаемое / ожидаемое число входных токенов в url+offsets | "
            f"{decisions['url_offsets_ratio']} |"
        )
    lines += ["", f"Решение «включать ли url-mode с offsets»: {_decision_text(decisions)}", ""]
    return "\n".join(lines)


# --- orchestration --------------------------------------------------------------------------
def plan_estimate(cfg: CalibrationConfig, settings: Settings) -> list[tuple[str, float, float]]:
    """(label, estimated tokens in, estimated USD) for each planned run. No API calls."""
    items: list[tuple[str, MediaRes, float]] = []
    if cfg.source_url:
        items.append(("1: url+offsets", settings.media_res_scan, cfg.url_window_len_s))
        items.append(
            (
                "1: url без offsets (если видео короткое)",
                settings.media_res_scan,
                cfg.url_full_max_duration_s,
            )
        )
    low: MediaRes = "low"
    high: MediaRes = "high"
    items += [
        ("3-4: file low", low, cfg.chunk_len_s),
        ("3-4: file high", high, cfg.chunk_len_s),
        ("5: agentic (оценка как static low)", low, cfg.chunk_len_s),
    ]
    items += [(f"6: thinking={lvl}", low, cfg.chunk_len_s) for lvl in cfg.thinking_levels]
    return [
        (
            label,
            float(estimate_video_tokens(d, res, settings)),
            estimate_video_cost(d, res, settings),
        )
        for label, res, d in items
    ]


def prepare(cfg: CalibrationConfig, settings: Settings, client: GeminiVideoClient) -> Prepared:
    from cliphunter.ingest.youtube import download_section, fetch_info
    from cliphunter.video.ffmpeg import proxy_cmd, run

    work = settings.data_dir / "calibration"
    work.mkdir(parents=True, exist_ok=True)
    chunk = work / "chunk_proxy.mp4"
    duration: float | None = None
    if cfg.source_file:
        src = Path(cfg.source_file)
        run(proxy_cmd(src, chunk, cfg.chunk_start_s, cfg.chunk_len_s), settings.ffmpeg_timeout_s)
    else:
        assert cfg.source_url
        info = fetch_info(cfg.source_url)
        raw_dur = info.get("duration")
        duration = float(raw_dur) if isinstance(raw_dur, int | float) else None
        section = work / "section.mp4"
        if not section.exists():
            download_section(
                cfg.source_url, cfg.chunk_start_s, cfg.chunk_start_s + cfg.chunk_len_s, section
            )
        run(proxy_cmd(section, chunk), settings.ffmpeg_timeout_s)
    uri, mime = client.upload(chunk)
    return Prepared(uri, mime, cfg.source_url, duration)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--config", type=Path)
    ap.add_argument("--live", action="store_true", help="really call the API (spends money)")
    ap.add_argument("--only", help="comma-separated experiment numbers, e.g. 3,4")
    ap.add_argument("--render-only", action="store_true", help="rebuild report from results.json")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a legacy codepage

    settings = get_settings()
    setup_logging(settings.log_level, settings.secrets())

    if args.render_only:
        runs: list[RunRecord] = []
        decisions: dict[str, Any] = {}
        meta: dict[str, Any] = {}
        if RESULTS_PATH.exists():
            data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
            runs = [RunRecord(**r) for r in data["runs"]]
            decisions, meta = data["decisions"], data["meta"]
        REPORT_PATH.write_text(render_report(runs, decisions, meta), encoding="utf-8")
        print(f"wrote {REPORT_PATH}")
        return 0

    if not args.config:
        ap.error("--config is required")
    cfg = load_config(args.config)

    if not settings.chunk_fits_context(settings.media_res_scan):
        print("WARNING: CHUNK_S exceeds the context share limit at MEDIA_RES_SCAN", file=sys.stderr)
    est = plan_estimate(cfg, settings)
    total = sum(e[2] for e in est)
    print(f"Plan ({'LIVE' if args.live else 'DRY RUN, nothing is sent'}):")
    for label, tok, usd in est:
        print(f"  {label:45s} ~{tok:>9,.0f} tokens in  ~{usd:7.4f} USD")
    print(
        f"  {'TOTAL (estimate)':45s} {'':>20s} ~{total:7.4f} USD "
        f"(cap CALIBRATION_MAX_COST_USD={settings.calibration_max_cost_usd})"
    )
    if not args.live:
        print("Dry run finished. Add --live to spend money.")
        return 0
    if not settings.gemini_api_key.get_secret_value():
        print("GEMINI_API_KEY is empty; put it in .env", file=sys.stderr)
        return 2

    client = GeminiVideoClient(settings, budget=Budget(settings.calibration_max_cost_usd))
    only = {int(x) for x in args.only.split(",")} if args.only else None
    prep = prepare(cfg, settings, client)
    live_runs = run_experiments(cfg, client, settings, prep, only)
    live_decisions = decide(live_runs, cfg)
    live_meta = {
        "date": date.today().isoformat(),
        "model": settings.gemini_model,
        "total_cost": client.budget.spent_usd,
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(
            {
                "runs": [asdict(r) for r in live_runs],
                "decisions": live_decisions,
                "meta": live_meta,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    REPORT_PATH.write_text(render_report(live_runs, live_decisions, live_meta), encoding="utf-8")
    print(f"spent {client.budget.spent_usd:.4f} USD; report: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
