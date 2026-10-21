# Отчёт по фазе 5: Экспорт в CapCut Desktop

*Дата: 21.10.2026 · Проект: ClipHunter · Статус: Реализована и готова к проверке*

## 1. Цель фазы
Реализация модуля экспорта клипов в формат черновиков **CapCut Desktop** согласно разделам 10 и 14 технического задания (`docs/plan.md`):
- Интерфейс `Exporter.export(clip) -> Path` с сохранением результата в `out/{job}/capcut_drafts/{clip_id}`.
- Содержимое черновика: видеоклип на таймлайне, текстовый заголовок-хук, при наличии — дорожка субтитров (`.ass` / `SubtitleWord`).
- Фиксация проверенной версии CapCut в `docs/DECISIONS.md`, smoke-тест на эталонной фикстуре схемы и описание переноса папки в каталог черновиков CapCut в `README.md`.
- Сквозная интеграция в пайплайн (`CAPCUT_EXPORT=false` по умолчанию), CLI, FastAPI и веб-интерфейс.

---

## 2. Что реализовано

### 2.1. Модуль экспорта черновиков (`src/cliphunter/export/capcut.py`)
1. **Протокол и модель данных:**
   - Протокол `Exporter` (`export(clip) -> Path`) и нормализованная спецификация `CapCutClipSpec`.
   - Класс `CapCutExporter(out_dir, copy_media=False)`, принимающий `CapCutClipSpec`, `ClipManifest` или `dict`.
   - Автоматическое определение разрешения (`width`, `height`), длительности и FPS готового клипа через `ffprobe` (с безопасным fallback для unit-тестов).
2. **Двойная совместимость схемы (`draft_content.json` + `draft_info.json`):**
   - Целевая версия **CapCut Desktop `9.6.0.4097` (Windows)** использует `draft_content.json` (`version: 360000`, `new_version: "138.0.0"`, `app_id: 359289`), а ранние сборки CapCut 5.x/6.x и утилиты `pyCapCut` / `VectCutAPI` читают `draft_info.json`.
   - Экспортер синхронно записывает оба файла, а также `draft_meta_info.json`, `draft_agency_config.json`, `draft_biz_config.json`, `draft_virtual_store.json`, `attachment_pc_common.json`, `timeline_layout.json` и `draft_settings`.
3. **Дорожки таймлайна (в микросекундах, `1 с = 1_000_000 мкс`):**
   - **`video_main` (`type: "video"`, `render_index: 0`):** видеосегмент с привязкой к `materials.videos`, `materials.speeds`, `materials.canvases`, `materials.sound_channel_mappings` и `materials.vocal_separations`. Автоматически выставляет пропорции холста `9:16` (`1080x1920`) или `16:9` (`1920x1080`).
   - **`title_hook` (`type: "text"`, `render_index: 15000`):** текстовый заголовок-хук на первые `3.5` с клипа (`transform.y = 0.65`, жирный белый шрифт с чёрной обводкой `border_width: 0.08` и полупрозрачной подложкой).
   - **`subtitles` (`type: "text"`, `render_index: 15001`, опционально):** парсер `parse_ass_subtitles()` и группировщик `group_subtitle_words()` формируют непересекающиеся субтитровые сегменты в нижней части экрана (`transform.y = -0.75`).
4. **Валидация, упаковка и установка:**
   - `validate_capcut_draft(draft_dir)`: проверяет наличие всех обязательных файлов, целостность ссылок `material_id` между `tracks` и `materials`, версию схемы и совпадение `tm_duration`.
   - `zip_capcut_draft(draft_dir)`: упаковывает черновик в `.zip`-архив для скачивания через браузер.
   - `install_draft_to_capcut(draft_dir, ...)`: копирует черновик напрямую в `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft\` и регистрирует проект в `root_meta_info.json`.

### 2.2. Интеграция в CLI, FastAPI и Web UI
- **CLI (`src/cliphunter/cli.py`):**
  - Флаг `--capcut` в команде `cliphunter run <target>`.
  - Новая команда `cliphunter capcut-export <mp4_или_папка> [--title ...] [--layout ...] [--install]`.
- **FastAPI (`src/cliphunter/api/routes_clips.py`, `routes_candidates.py`):**
  - `POST /api/jobs/{job_id}/render` принимает флаг `capcut_export: bool | None`.
  - `POST /api/clips/{clip_id}/capcut` генерирует и валидирует черновик на диске.
  - `GET /api/clips/{clip_id}/capcut/download` отдаёт `.zip`-архив черновика (`application/zip`).
- **Web UI (`src/cliphunter/web/index.html`, `static/app.js`):**
  - В панели рендера добавлен чекбокс «Черновик CapCut».
  - На карточках готовых клипов на экране «Результаты» добавлена кнопка **`🎬 CapCut (.zip)`**.

---

## 3. Что измерено и проверено

1. **Демо-черновики на реальных клипах (`data/demo_clips/capcut_drafts/`):**
   - `01_Невероятный_клатч_1_в_4_original` — `1920x1080` (`16:9`), `4_000_000` мкс (`4.00` с), 1 видеодорожка + 1 текстовая дорожка заголовка.
   - `02_Реакция_на_донат_100_000_руб_blur_fit_9x16` — `1080x1920` (`9:16`), `4_000_000` мкс (`4.00` с), 1 видеодорожка + 1 текстовая дорожка заголовка.
   - `03_Стрим__Evelone192_эпичный_момент_facecam_top_game_bottom_9x16` — `1080x1920` (`9:16`), `4_000_000` мкс (`4.00` с), 1 видеодорожка + 1 текстовая дорожка заголовка.
2. **Smoke-тест на эталонной фикстуре (`tests/fixtures/capcut_9_6_0_reference.json`):**
   - Проверено соответствие всех обязательных ключей `draft_content.json`, `draft_info.json`, `draft_meta_info.json`, `materials.videos`, `materials.texts` и `tracks` установленной версии CapCut Desktop `9.6.0.4097`.
3. **Тесты и статический анализ:**
   - `uv run pytest -q`: **114 passed** (добавлены `tests/unit/test_capcut.py` и `tests/integration/test_capcut_export.py`).
   - `uv run ruff check .` и `uv run ruff format --check .`: **0 ошибок**.
   - `uv run mypy`: **Success: no issues found in 54 source files**.

---

## 4. Допущения (записаны в `docs/DECISIONS.md`)
- Зафиксирована установленная на рабочей машине версия **CapCut Desktop `9.6.0.4097`** (`version: 360000`).
- Для максимальной совместимости одновременно создаются `draft_content.json` и `draft_info.json`.
- По умолчанию видеоматериал ссылается на абсолютный путь отрендеренного `.mp4` файла (при `copy_media=True` видео также копируется внутрь `Resources/video/` черновика).
