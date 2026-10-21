# ClipHunter

Gemini смотрит запись стрима (кадры и звук, без транскрипции), находит лучшие моменты, приложение вырезает их и отдаёт клипы. Полное ТЗ: [docs/plan.md](docs/plan.md).

**Статус:** фаза 3 (Сервис, API и Ревью UI — готова к проверке). См. [docs/PROGRESS.md](docs/PROGRESS.md).

## Быстрый старт (Windows, ничего не ставится в систему)

Нужны `uv` и `ffmpeg` в PATH (Docker опционален — по умолчанию база SQLite работает автономно).

```powershell
uv sync --python 3.12          # создаёт .venv с Python 3.12 и зависимостями
copy .env.example .env         # впиши GEMINI_API_KEY
uv run pytest -q               # 100 тестов проходят без платных вызовов
uv run ruff check . ; uv run ruff format --check . ; uv run mypy
```

Ключи только в `.env` (он в `.gitignore`). Логи маскируют ключи.

## Веб-интерфейс и API (Фаза 3)

Запуск веб-сервера и интерфейса ревью:
```powershell
uv run cliphunter serve
# или с авто-перезагрузкой:
uv run cliphunter serve --reload --port 8000
```
После запуска открой в браузере: **`http://127.0.0.1:8000`**

### Экраны интерфейса:
1. **Новая задача:** ввод ссылки на YouTube или перетаскивание локального видеофайла, выбор профиля стримера (универсальный, evelone2004, шутеры, chatting), настройка мин/макс длительности клипов, лимит бюджета и кнопка предварительной оценки стоимости (`/api/jobs/estimate`).
2. **Ход задачи:** интерактивный степпер этапов (S0 Ingest → S1 Proxy → S2 Plan → S3 Scan → S4 Merge → S5 Refine → S6 Judge → S7 Cut), отображение текущего расхода USD и числа кандидатов, консоль событий в реальном времени по Server-Sent Events (SSE), кнопки отмены и перезапуска.
3. **Ревью клипов:** встроенный видеоплеер с HTTP 206 потоковым воспроизведением и перемоткой прокси-видео, интерактивный скраббер с визуальными маркерами моментов, список кандидатов с карточками вирусности, кнопками подгонки границ с шагом **±0.1 с** и **±1.0 с**, форма ручного добавления момента (`POST /api/jobs/{id}/candidates`), выбор layout кадра и кнопка запуска рендера.
4. **Результаты:** карточки готовых смонтированных клипов с предпросмотром в плеере, кнопка скачивания `.mp4`, копирование заголовков и модальное окно детальной статистики расходов и вызовов API Gemini (`/api/jobs/{id}/usage`).

OpenAPI документация доступна по адресу: `http://127.0.0.1:8000/docs`.

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

## Оценка качества и бенчмарк (Фаза 2)

Скрипт `scripts/eval.py` рассчитывает Recall (IoU >= 0.5), Precision, F1, Mean Boundary Error до и после Refine+Snap, скорость и стоимость на эталонных стримах:

```powershell
# Сухой запуск бенчмарка на синтетических эталонах (0 USD):
uv run python scripts/eval.py --dry-run
# или через Makefile:
make eval-dry

# Запуск на размеченных стримах из папки golden/:
uv run python scripts/eval.py
```
Отчёты сохраняются в `eval/reports/eval_<timestamp>.md` и `.json`.

## Структура

См. раздел 5 плана. Реализованы модули:
- `api/` (FastAPI роутеры: uploads, jobs, candidates, clips, profiles, media range streaming, SSE broadcaster)
- `db/` (SQLAlchemy 2.0 async ORM models, session manager, CRUD repo, Alembic миграции)
- `worker/` (Асинхронный фоновый менеджер задач, шлюз awaiting_review, рендерер)
- `web/` (Single Page App: dark-theme интерфейс нарезчика, 4 экрана, плеер, скраббер, модальные окна)
- `cli.py` (CLI интерфейс: run, estimate, serve)
- `models.py` (Domain models, Pydantic v2)
- `ingest/` (`probe.py`, `files.py`, `youtube.py`)
- `pipeline/` (`stages.py`, `chunking.py`, `merge.py`, `validate.py`, `timecodes.py`)
- `llm/` (`client.py`, `scan.py`, `refine.py`, `judge.py`, `cache.py`, `budget.py`, `schemas.py`, `prompts.py`)
- `video/` (`ffmpeg.py`, `cut.py`, `snap.py`)
- `export/` (`manifest.py`)
- `scripts/` (`calibrate.py`, `eval.py`)

Допущения: [docs/DECISIONS.md](docs/DECISIONS.md).

