# ClipHunter

Gemini смотрит запись стрима (кадры и звук, без транскрипции), находит лучшие моменты, приложение вырезает их и отдаёт клипы. Полное ТЗ: [docs/plan.md](docs/plan.md).

**Статус:** фаза 1 (CLI MVP). См. [docs/PROGRESS.md](docs/PROGRESS.md).

## Быстрый старт (Windows, ничего не ставится в систему)

Нужны `uv`, `ffmpeg` в PATH и Docker Desktop (Docker только для Postgres и Redis, в фазе 1 не обязателен).

```powershell
uv sync --python 3.12          # создаёт .venv с Python 3.12 и зависимостями
copy .env.example .env         # впиши GEMINI_API_KEY
uv run pytest -q               # 74 теста проходят за пару секунд
uv run ruff check . ; uv run ruff format --check . ; uv run mypy
```

Ключи только в `.env` (он в `.gitignore`). Логи маскируют ключи.

## Использование CLI (Фаза 1)

1. **Предварительная оценка стоимости:**
   ```powershell
   uv run cliphunter estimate "https://www.youtube.com/watch?v=XXXXXXXXXXX"
   # или для локального файла:
   uv run cliphunter estimate C:\path\to\stream.mp4
   ```

2. **Запуск полного пайплайна (S0–S4, S7):**
   ```powershell
   # Запуск с запросом подтверждения бюджета, если стоимость > CONFIRM_ABOVE_USD
   uv run cliphunter run "https://www.youtube.com/watch?v=XXXXXXXXXXX" --profile profiles/default.yaml

   # Запуск с автоматическим подтверждением бюджета (--yes):
   uv run cliphunter run C:\path\to\stream.mp4 --yes --out data/out/my_stream
   ```

3. **Результат работы:**
   - Готовые клипы в формате `.mp4` с покадровой точностью и затуханиями аудио в папке вывода `data/out/{job_id}/` (или `--out`).
   - `manifest.json` задачи и манифест каждого клипа.
   - Идемпотентность и возобновление: повторный запуск на том же видео берёт всё из кэша и стоит 0 USD. В случае сбоя пайплайн автоматически продолжает работу с последнего завершённого этапа (`state.json`).

## Калибровка (фаза 0)

1. Скопируй `golden/calibration.example.yaml` в `calibration/calibration.yaml`: ссылка на публичный стрим, окно 10 минут и эталонные моменты.
2. Сухой прогон (0 USD):
   ```powershell
   uv run python scripts/calibrate.py --config calibration/calibration.yaml
   ```
3. Платный прогон:
   ```powershell
   uv run python scripts/calibrate.py --config calibration/calibration.yaml --live
   ```

Все платные вызовы пишутся в `data/logs/api_calls.jsonl` (модель, режим, токены, цена, время), ответы кэшируются в `data/cache/llm/`.

## Структура

См. раздел 5 плана. Реализованы модули:
- `cli.py` (CLI интерфейс)
- `models.py` (Domain models, Pydantic)
- `ingest/` (`probe.py`, `files.py`, `youtube.py`)
- `pipeline/` (`stages.py`, `chunking.py`, `merge.py`, `validate.py`, `timecodes.py`)
- `llm/` (`client.py`, `scan.py`, `cache.py`, `budget.py`, `schemas.py`, `prompts.py`)
- `video/` (`ffmpeg.py`, `cut.py`)
- `export/` (`manifest.py`)
- `scripts/calibrate.py`

Допущения: [docs/DECISIONS.md](docs/DECISIONS.md).
