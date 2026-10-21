# ClipHunter Setup & Installation Script for Windows (PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File scripts/install.ps1

$ErrorActionPreference = "Stop"

Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  ClipHunter — Установка и настройка окружения " -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

# 1. Check uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Утилита 'uv' не найдена в PATH." -ForegroundColor Red
    Write-Host "Установите uv: https://docs.astral.sh/uv/getting-started/installation/" -ForegroundColor Yellow
    exit 1
}
Write-Host "[1/5] uv найден: $(uv --version)" -ForegroundColor Green

# 2. Check FFmpeg & FFprobe
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    Write-Host "[WARNING] ffmpeg или ffprobe не найдены в PATH. Убедитесь, что FFmpeg установлен для нарезки видео." -ForegroundColor Yellow
} else {
    Write-Host "[2/5] FFmpeg и FFprobe найдены в PATH" -ForegroundColor Green
}

# 3. Sync Python 3.12 virtualenv and dependencies
Write-Host "[3/5] Синхронизация виртуального окружения Python 3.12 (.venv)..." -ForegroundColor Cyan
uv sync --python 3.12

# 4. Create .env from .env.example if missing
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "[4/5] Создан файл .env из шаблона .env.example (впишите GEMINI_API_KEY в .env)" -ForegroundColor Yellow
    }
} else {
    Write-Host "[4/5] Файл .env уже существует" -ForegroundColor Green
}

# 5. Initialize directories, database, and run diagnostics
Write-Host "[5/5] Инициализация базы данных, профилей и проверка окружения..." -ForegroundColor Cyan
uv run python scripts/check_env.py --init-db

Write-Host ""
Write-Host "Установка успешно завершена!" -ForegroundColor Green
Write-Host "Быстрый запуск:"
Write-Host "  • Веб-интерфейс и API:  uv run cliphunter serve"
Write-Host "  • CLI поиск клипов:     uv run cliphunter run <URL_или_файл>"
Write-Host "  • Telegram-бот:         uv run cliphunter bot"
Write-Host "  • Тесты:                uv run pytest -q"
