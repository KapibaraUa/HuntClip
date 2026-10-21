.PHONY: install setup check-env lint fmt test up down calibrate-dry eval eval-dry backup cleanup serve bot

install:
	uv sync --python 3.12

setup: install
	uv run python scripts/check_env.py --init-db

check-env:
	uv run python scripts/check_env.py

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

fmt:
	uv run ruff format .
	uv run ruff check . --fix

test:
	uv run pytest -q

up:
	docker compose up -d

down:
	docker compose down

# Prints the calibration plan and cost estimate. Sends nothing to the API.
calibrate-dry:
	uv run python scripts/calibrate.py --config calibration/calibration.yaml

# Runs quality benchmark on golden sets and outputs markdown report.
eval:
	uv run python scripts/eval.py

# Runs dry-run evaluation on synthetic benchmark fixtures without API calls.
eval-dry:
	uv run python scripts/eval.py --dry-run

# Creates a compressed database snapshot in data/backups/
backup:
	uv run cliphunter backup

# Purges expired temporary files and rotates logs according to RETENTION_DAYS
cleanup:
	uv run cliphunter cleanup

# Launches FastAPI Web Review UI
serve:
	uv run cliphunter serve

# Launches Telegram Bot worker
bot:
	uv run cliphunter bot
