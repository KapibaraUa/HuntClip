# ClipHunter

Gemini смотрит запись стрима (кадры и звук, без транскрипции), находит лучшие моменты, приложение вырезает их и отдаёт клипы. Полное ТЗ: [docs/plan.md](docs/plan.md).

**Статус:** фаза 8 (Финальная сквозная интеграция и E2E — готова к проверке; все фазы 0–8 реализованы на 100%). См. [docs/PROGRESS.md](docs/PROGRESS.md) и [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

## Быстрый старт (Windows / Linux, ничего не ставится в систему)

Нужны `uv` и `ffmpeg` в PATH (Docker опционален — по умолчанию база SQLite работает автономно).

```powershell
# Автоматическая установка, создание .env, инициализация БД и проверка окружения:
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
# (или на Linux/macOS/WSL: bash scripts/install.sh)

# Или вручную:
uv sync --python 3.12                      # создаёт .venv с Python 3.12 и зависимостями
copy .env.example .env                     # впиши GEMINI_API_KEY
uv run python scripts/check_env.py --init-db
uv run pytest -q                           # 137 тестов проходят без платных вызовов
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

## Экспорт черновиков в CapCut Desktop (Фаза 5)

По умолчанию экспорт в CapCut выключен (`CAPCUT_EXPORT=false` в `.env`), но его можно включить глобально, через галочку «Черновик CapCut» в веб-интерфейсе, флагом `--capcut` в CLI или отдельной командой `cliphunter capcut-export`.

- **Проверенная версия CapCut Desktop:** `9.6.0.4097` (Windows, схема `version: 360000`). Рекомендуется отключить автообновление CapCut, чтобы обновления приложения не меняли формат локальных черновиков.
- **Структура черновика:** сохраняется в `out/{job}/capcut_drafts/{clip_id}/` (содержит `draft_content.json`, `draft_info.json`, `draft_meta_info.json` и служебные конфиги; на таймлайне размещаются видеоклип `video_main`, заголовок-хук `title_hook` и при наличии — дорожка субтитров `subtitles`).

### Как открыть черновик в CapCut:

1. **Автоматическая установка через CLI (`--install`):**
   ```powershell
   uv run cliphunter capcut-export data/demo_clips/03_Стрим__Evelone192_эпичный_момент_facecam_top_game_bottom_9x16.mp4 --title "Эпичный момент Evelone192" --layout facecam_top_game_bottom_9x16 --install
   ```
   Команда создаст черновик и автоматически скопирует его в папку проектов CapCut (`%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\`).

2. **Ручное копирование папки черновика:**
   - Скопируй папку `out/{job}/capcut_drafts/{clip_id}` (или распакованный `.zip` из веб-интерфейса) в каталог черновиков CapCut:
     ```text
     %LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\
     ```
   - Запусти (или перезапусти) CapCut Desktop — проект появится первым в списке на главном экране с готовым клипом и заголовком на таймлайне.

## Сервисные удобства (Фаза 6)

### 1. Telegram-бот (`aiogram 3`)
Позволяет стримерам и нарезчикам работать через Telegram прямо со смартфона:
```powershell
# Запуск бота (токен берётся из TELEGRAM_TOKEN в .env или флагом):
uv run cliphunter bot
uv run cliphunter bot --token "123456:ABC-DEF..."
```
- **Возможности:**
  - Отправь боту ссылку на YouTube/Twitch или загрузи видеофайл прямо в чат.
  - Бот рассчитает токеноёмкость и покажет оценку стоимости Gemini с кнопками `[🚀 Запустить]` и `[❌ Отмена]`.
  - В процессе работы статусное сообщение непрерывно обновляется с текущим этапом пайплайна (S0 Ingest → S1 Proxy → S2 Chunk → S3 Scan → S4 Merge → S5 Refine → S6 Judge → S7 Cut).
  - Готовые `.mp4` клипы (до 50 МБ) бот присылает прямо в чат как видеозаписи.
  - Ограничение доступа: белый список `TELEGRAM_ALLOWED_IDS` в `.env`.
  - Команды: `/start`, `/help`, `/jobs`, `/cancel <job_id>`, `/profiles` (выбор активного пресета стримера).

### 2. Запись и прогрессивный анализ идущих стримов (F10)
Автоматически подключается к live-трансляции YouTube Live, Twitch или Kick, режет поток на куски по `CHUNK_S` и параллельно анализирует их в фоне:
```powershell
# Запись и нарезка стрима сегментами по 45 минут:
uv run cliphunter live "https://www.youtube.com/live/XXXXXXXXXXX" --chunk-s 2700 --profile evelone2004 --yes
```
Клипы и кандидаты появляются в БД, SSE-стриме и ревью-интерфейсе прямо по ходу трансляции!

### 3. Пакетная обработка очередей ссылок (`batch`)
Позволяет поставить в очередь на обработку несколько стримов сразу:
```powershell
# Список ссылок аргументами:
uv run cliphunter batch "https://youtube.com/watch?v=1" "https://twitch.tv/videos/2" --profile gaming_action --yes

# Из текстового файла (одна ссылка на строку, строки с # игнорируются):
uv run cliphunter batch --file streams.txt --yes --capcut
```
Также доступен API эндпоинт `POST /api/jobs/batch`.

### 4. Редактор профилей стримеров в Web UI
В веб-интерфейсе по кнопке **«⚙️ Профили»** доступно модальное окно управления пресетами:
- Редактирование координат вебкамеры (`facecam_rect`: X, Y, W, H от 0 до 1) и геймплея.
- Настройка мин/макс хронометража клипов и языка.
- Промпт-надстройка для подсказок Gemini (характерный стиль стрима, шутки, реакции).
- Создание новых профилей и мгновенное сохранение через `PUT /api/profiles/{name}`.

## Обслуживание, резервное копирование и ротация данных (Фаза 7)

### 1. Резервное копирование и восстановление БД (`backup`)
Создаёт сжатый онлайн-снимок базы данных (`data/backups/cliphunter_backup_YYYYMMDD_HHMMSS.sqlite3.gz`) и автоматически удаляет старые архивы сверх лимита `--keep`:
```powershell
uv run cliphunter backup --keep 7
uv run cliphunter backup --list
uv run cliphunter backup --restore data/backups/cliphunter_backup_20261021_120000.sqlite3.gz
```

### 2. Очистка устаревших файлов и ротация логов (`cleanup`)
Удаляет временные исходники (`data/sources`), прокси (`data/proxies`), загрузки (`data/uploads`), кэш LLM и рабочие файлы задач (`data/jobs/*/work/*.mp4`) старше `RETENTION_DAYS` (14 дней по умолчанию), **сохраняя** `state.json` и готовые смонтированные клипы в `out/`. Также сжимает логи (`data/logs/api_calls.jsonl` → `.gz`):
```powershell
# Безопасный предпросмотр без удаления:
uv run cliphunter cleanup --dry-run

# Очистка файлов старше 14 дней и ротация логов:
uv run cliphunter cleanup --days 14 --force-log-rotate
```
Также доступны системные API эндпоинты: `GET /api/system/storage`, `POST /api/system/backup`, `POST /api/system/cleanup`.

### 3. Известные ограничения
Полный перечень лимитов Gemini Interactions API, `yt-dlp`, CapCut Desktop `9.6.0.4097`, Telegram Bot API (50 МБ) и особенностей SQLite/PostgreSQL задокументирован в [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

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
- `api/` (FastAPI роутеры: uploads, jobs, candidates, clips, profiles, system, media range streaming, SSE broadcaster)
- `bot/` (Telegram-бот aiogram 3: ссылки, файлы, подтверждения, стриминг прогресса, доставка клипов)
- `db/` (SQLAlchemy 2.0 async ORM models, session manager, CRUD repo, Alembic миграции)
- `maintenance/` (`backup.py`, `cleanup.py`: бэкап/восстановление БД, очистка по `RETENTION_DAYS`, ротация логов)
- `worker/` (Асинхронный фоновый менеджер задач, шлюз awaiting_review, рендерер)
- `web/` (Single Page App: dark-theme интерфейс нарезчика, 4 экрана, плеер, скраббер, редактор профилей, модальные окна)
- `cli.py` (CLI интерфейс: run, estimate, capcut-export, serve, bot, live, batch, backup, cleanup)
- `models.py` (Domain models, Pydantic v2)
- `ingest/` (`probe.py`, `files.py`, `youtube.py`, `live.py`)
- `pipeline/` (`stages.py`, `chunking.py`, `merge.py`, `validate.py`, `timecodes.py`)
- `llm/` (`client.py`, `scan.py`, `refine.py`, `judge.py`, `cache.py`, `budget.py`, `schemas.py`, `prompts.py`)
- `video/` (`ffmpeg.py`, `cut.py`, `snap.py`, `layouts.py`, `render.py`, `template.py`, `subtitles.py`)
- `export/` (`manifest.py`, `capcut.py`)
- `scripts/` (`calibrate.py`, `eval.py`, `check_env.py`, `install.ps1`, `install.sh`)

Допущения: [docs/DECISIONS.md](docs/DECISIONS.md).

