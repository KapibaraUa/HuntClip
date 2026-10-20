# ClipHunter

Gemini смотрит запись стрима (кадры и звук, без транскрипции), находит лучшие моменты, приложение вырезает их и отдаёт клипы. Полное ТЗ: [docs/plan.md](docs/plan.md).

**Статус:** фаза 0 (основа и калибровка). См. [docs/PROGRESS.md](docs/PROGRESS.md).

## Быстрый старт (Windows, ничего не ставится в систему)

Нужны `uv`, `ffmpeg` в PATH и Docker Desktop (Docker только для Postgres и Redis, в фазе 0 не обязателен).

```powershell
uv sync --python 3.12          # создаёт .venv с Python 3.12 и зависимостями
copy .env.example .env         # впиши GEMINI_API_KEY
uv run pytest -q               # тесты, сеть и ключ не нужны
uv run ruff check . ; uv run ruff format --check . ; uv run mypy
docker compose up -d           # Postgres и Redis
```

Ключи только в `.env` (он в `.gitignore`). Логи маскируют ключи.

## Калибровка (фаза 0)

1. Скопируй `golden/calibration.example.yaml` в `calibration/calibration.yaml`: ссылка на публичный стрим, окно 10 минут и эталонные моменты (секунды от начала всего видео).
2. Сухой прогон: ничего не отправляется, печатается план и оценка стоимости.

   ```powershell
   uv run python scripts/calibrate.py --config calibration/calibration.yaml
   ```
3. Платный прогон (только после твоего решения), потолок трат `CALIBRATION_MAX_COST_USD`:

   ```powershell
   uv run python scripts/calibrate.py --config calibration/calibration.yaml --live
   ```

   Он перезапишет [docs/CALIBRATION.md](docs/CALIBRATION.md) и положит сырые результаты в `calibration/results.json`. Затем перенеси `URL_OFFSETS_OK` и `URL_TS_RELATIVE_TO_WINDOW` из отчёта в `.env.example`.

Все платные вызовы пишутся в `data/logs/api_calls.jsonl` (модель, режим, токены, цена, время), ответы кэшируются в `data/cache/llm/`.

## Структура

См. раздел 5 плана. Сейчас реализовано: `config.py`, `logging.py`, `llm/` (схемы, бюджет, кэш, клиент, промпты), `pipeline/timecodes.py`, `video/ffmpeg.py`, `ingest/youtube.py`, `scripts/calibrate.py`. Остальные модули появятся в своих фазах.

Допущения: [docs/DECISIONS.md](docs/DECISIONS.md).
