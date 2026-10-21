#!/usr/bin/env bash
# ClipHunter Setup & Installation Script for Linux / macOS / WSL
# Usage: bash scripts/install.sh

set -euo pipefail

echo "==============================================="
echo "  ClipHunter — Установка и настройка окружения "
echo "==============================================="

if ! command -v uv >/dev/null 2>&1; then
    echo "[ERROR] Утилита 'uv' не найдена в PATH."
    echo "Установите uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
echo "[1/5] uv найден: $(uv --version)"

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    echo "[WARNING] ffmpeg или ffprobe не найдены в PATH. Установите пакет ffmpeg."
else
    echo "[2/5] FFmpeg и FFprobe найдены в PATH"
fi

echo "[3/5] Синхронизация виртуального окружения Python 3.12 (.venv)..."
uv sync --python 3.12

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp ".env.example" ".env"
    echo "[4/5] Создан файл .env из шаблона .env.example (впишите GEMINI_API_KEY в .env)"
else
    echo "[4/5] Файл .env готов"
fi

echo "[5/5] Инициализация базы данных, профилей и проверка окружения..."
uv run python scripts/check_env.py --init-db

echo ""
echo "Установка успешно завершена!"
echo "Быстрый запуск:"
echo "  • Веб-интерфейс и API:  uv run cliphunter serve"
echo "  • CLI поиск клипов:     uv run cliphunter run <URL_или_файл>"
echo "  • Telegram-бот:         uv run cliphunter bot"
echo "  • Тесты:                uv run pytest -q"
