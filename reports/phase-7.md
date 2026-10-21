# Отчёт по фазе 7: Полировка

*Дата: 21.10.2026 · Проект: ClipHunter · Статус: Реализована и готова к проверке (финальная фаза ТЗ)*

## 1. Цель фазы
Завершение разработки и эксплуатационная полировка **ClipHunter** согласно техническому заданию (`docs/plan.md`, разделы 12 и 14, Фаза 7):
1. **Резервное копирование и восстановление БД (`src/cliphunter/maintenance/backup.py`):**
   - Безопасный онлайн-бэкап SQLite (`sqlite3.Connection.backup()`) и PostgreSQL (`pg_dump`) со сжатием в `.gz`.
   - Автоматическая ротация архивов с сохранением `keep_last` (по умолчанию 7) последних снимков.
   - Восстановление из архива с проверкой `PRAGMA integrity_check` и созданием резервной копии `.pre_restore.bak`.
2. **Ротация логов и очистка данных по `RETENTION_DAYS` (`src/cliphunter/maintenance/cleanup.py`):**
   - Очистка устаревших исходников (`data/sources`), прокси (`data/proxies`), загрузок (`data/uploads`), live-сегментов (`data/live`), кэша LLM (`data/cache/llm`) и временных рабочих файлов задач (`data/jobs/*/work/*.mp4`) старше `RETENTION_DAYS` (14 дней по умолчанию).
   - Гарантированное сохранение состояния задач (`state.json`) и готовых смонтированных клипов (`data/jobs/*/out/`).
   - Сжатие и ротация лог-файлов (`data/logs/api_calls.jsonl`, `*.log` → `.gz`) без обрыва открытых файловых дескрипторов.
3. **Скрипты установки и диагностики окружения:**
   - Кроссплатформенный диагностический скрипт `scripts/check_env.py` (`--init-db`, `--json`).
   - Скрипт установки для Windows PowerShell (`scripts/install.ps1`) и Linux/macOS/WSL (`scripts/install.sh`).
4. **Список известных ограничений (`docs/LIMITATIONS.md`):**
   - Документирование лимитов Gemini Interactions API / Files API, закрытых стримов, привязки к версии CapCut Desktop `9.6.0.4097`, лимита 50 МБ в Telegram Bot API и особенностей SQLite/PostgreSQL.
5. **Финальная документация (`README.md`, `docs/DECISIONS.md`, `docs/PROGRESS.md`, `Makefile`, `.env.example`).**

---

## 2. Что реализовано

### 2.1. Модуль резервного копирования БД (`src/cliphunter/maintenance/backup.py`)
- **`backup_database(settings, out_dir, keep_last=7)`:**
  - Для SQLite создаёт горячий снимок через встроенный механизм `src_conn.backup(dst_conn)`, сжимает через `gzip` в `data/backups/cliphunter_backup_YYYYMMDD_HHMMSS.sqlite3.gz`.
  - Для PostgreSQL вызывает `pg_dump` и сжимает дамп в `.sql.gz`.
  - Вызывает `rotate_backups()`, удаляя устаревшие архивы сверх лимита `keep_last`.
- **`restore_database(backup_path, settings)`:**
  - Распаковывает `.sqlite3.gz` во временный файл, проверяет целостность через `PRAGMA integrity_check;`, сохраняет текущую БД в `.pre_restore.bak` и атомарно заменяет рабочий файл БД.
- **`list_backups(out_dir, settings)`:**
  - Возвращает отсортированный список доступных архивов с размером и временем создания.

### 2.2. Модуль очистки данных и ротации логов (`src/cliphunter/maintenance/cleanup.py`)
- **`cleanup_expired_data(settings, retention_days, dry_run, rotate_log_files, force_log_rotate)`:**
  - Сканирует каталоги временных и кэшированных данных, удаляет файлы старше `retention_days` (с поддержкой безопасного предпросмотра `dry_run=True`).
  - Не затрагивает `state.json` и финальные клипы/черновики в `data/jobs/*/out/`.
  - Возвращает детальную сводку `CleanupReport` (`deleted_files_count`, `freed_bytes`, `freed_mb`, `rotated_logs_count`).
- **`rotate_logs(logs_dir, max_size_bytes=10MB, keep_archives=10, force=False, dry_run=False)`:**
  - Архивирует `api_calls.jsonl` и `.log` файлы в `api_calls_YYYYMMDD_HHMMSS.jsonl.gz` и очищает активный файл для продолжения записи.
- **`get_storage_stats(settings)`:**
  - Подсчитывает количество файлов и занимаемый объём (в байтах и МБ) по каждой подпапке `data/`.

### 2.3. Интеграция в CLI и FastAPI
- **CLI (`src/cliphunter/cli.py`):**
  - `cliphunter backup [--out-dir DIR] [--keep N] [--restore PATH] [--list]`
  - `cliphunter cleanup [--days N] [--dry-run] [--force-log-rotate]`
- **FastAPI (`src/cliphunter/api/routes_system.py`, подключён в `api/app.py`):**
  - `GET /api/system/storage` — статистика дискового пространства по категориям и список бэкапов.
  - `POST /api/system/backup` — создание сжатого бэкапа БД.
  - `POST /api/system/cleanup` — запуск очистки устаревших данных и ротации логов.

### 2.4. Скрипты установки и проверки окружения
- **`scripts/check_env.py`:** проверяет версию Python (>=3.12), `ffmpeg`, `ffprobe`, наличие аппаратного кодека `h264_nvenc`, пакет `yt-dlp`, наличие `.env`, ключей и директории черновиков CapCut Desktop. При передаче `--init-db` создаёт структуру каталогов `data/`, инициализирует схему БД и заполняет стартовые профили стримеров (`default`, `evelone2004`, `gaming_action`, `just_chatting`).
- **`scripts/install.ps1` и `scripts/install.sh`:** выполняют установку одной командой (проверка `uv` и `ffmpeg`, `uv sync --python 3.12`, создание `.env` из `.env.example`, инициализация БД и профилей).

### 2.5. Документация и ограничения
- Создан документ [`docs/LIMITATIONS.md`](../docs/LIMITATIONS.md) с описанием всех известных ограничений (Gemini API, `yt-dlp`, CapCut Desktop `9.6.0.4097`, Telegram 50 МБ, FFmpeg/Whisper, SQLite/PostgreSQL).
- Обновлены [`README.md`](../README.md), [`docs/DECISIONS.md`](../docs/DECISIONS.md), [`docs/PROGRESS.md`](../docs/PROGRESS.md), [`Makefile`](../Makefile) и [`.env.example`](../.env.example).

---

## 3. Что измерено и проверено

1. **Проверка `scripts/check_env.py --init-db` на целевой машине:**
   - Python `3.12.14`: OK
   - FFmpeg `9.0.2-full_build` и FFprobe: OK
   - Аппаратное ускорение `h264_nvenc`: Available
   - Модуль `yt-dlp`: OK
   - Каталог черновиков CapCut Desktop: Found
   - База данных инициализирована, создано 4 стартовых профиля стримеров.
2. **Проверка CLI команд `backup` и `cleanup`:**
   - `uv run cliphunter backup` создал сжатый архив `data/backups/cliphunter_backup_*.sqlite3.gz` (2024 байт).
   - `uv run cliphunter cleanup --dry-run` корректно просканировал хранилище и вывел отчёт.
3. **Автоматические тесты (`tests/unit/test_maintenance.py`, `tests/integration/test_system_api.py`):**
   - Полный цикл `backup_database` → удаление записей → `restore_database` с проверкой целостности `PRAGMA integrity_check` и восстановления строк таблицы.
   - Ротация бэкапов (`keep_last=2` из 4 архивов).
   - Очистка просроченных файлов (`mtime` > 14 дней) с проверкой сохранения `state.json` и готовых клипов в `out/`.
   - Сжатие и обнуление логов `api_calls.jsonl` → `.jsonl.gz`.
   - Интеграционные тесты системного API (`/api/system/storage`, `/api/system/backup`, `/api/system/cleanup`) и CLI.
4. **Общий прогон тестов и линтеров по всему проекту:**
   - `uv run pytest -q`: **137 passed** (все тесты фаз 0–7 проходят).
   - `uv run ruff check .`: **All checks passed!**
   - `uv run ruff format --check .`: **все файлы отформатированы**.
   - `uv run mypy`: **0 ошибок типизации**.
