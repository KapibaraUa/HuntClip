"""Environment verification and initialization script for ClipHunter (Phase 7)."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from cliphunter.api.routes_profiles import DEFAULT_PROFILES
from cliphunter.config import get_settings
from cliphunter.db.repo import list_profiles, upsert_profile
from cliphunter.db.session import get_db_manager, init_db
from cliphunter.export.capcut import get_default_capcut_drafts_dir


def check_binary(name: str, version_arg: str = "-version") -> dict[str, Any]:
    """Checks if an external binary is available in PATH and grabs its first version line."""
    bin_path = shutil.which(name)
    if not bin_path:
        return {"available": False, "path": None, "version": None}
    try:
        proc = subprocess.run(
            [bin_path, version_arg],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        first_line = (
            (proc.stdout or proc.stderr or "").splitlines()[0]
            if (proc.stdout or proc.stderr)
            else ""
        )
        return {"available": True, "path": bin_path, "version": first_line.strip()}
    except Exception as exc:
        return {"available": False, "path": bin_path, "version": str(exc)}


def check_nvenc_support() -> bool:
    """Checks whether FFmpeg has the h264_nvenc hardware encoder compiled in."""
    if not shutil.which("ffmpeg"):
        return False
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return "h264_nvenc" in (proc.stdout or "")
    except Exception:
        return False


def ensure_data_dirs(data_dir: Path) -> list[str]:
    """Creates all required ClipHunter runtime subdirectories."""
    subdirs = [
        "sources",
        "proxies",
        "jobs",
        "uploads",
        "live",
        "backups",
        "logs",
        "cache/llm",
    ]
    created: list[str] = []
    for sub in subdirs:
        p = data_dir / sub
        p.mkdir(parents=True, exist_ok=True)
        created.append(str(p))
    return created


async def initialize_database() -> int:
    """Initializes database tables and seeds default streamer profiles."""
    await init_db()
    db = get_db_manager()
    async with db.session_factory() as session:
        existing = await list_profiles(session)
        if not existing:
            for prof in DEFAULT_PROFILES:
                await upsert_profile(session, prof)
            await session.commit()
            existing = await list_profiles(session)
    await db.close()
    return len(existing)


def run_environment_checks(do_init_db: bool = False) -> dict[str, Any]:
    """Executes all environment diagnostics and returns a structured status dictionary."""
    settings = get_settings()
    data_dir = Path(settings.data_dir)
    created_dirs = ensure_data_dirs(data_dir)

    py_ok = sys.version_info >= (3, 12)
    ffmpeg_info = check_binary("ffmpeg", "-version")
    ffprobe_info = check_binary("ffprobe", "-version")
    nvenc_ok = check_nvenc_support()

    ytdlp_ok = importlib.util.find_spec("yt_dlp") is not None

    capcut_root = get_default_capcut_drafts_dir()
    env_file_exists = Path(".env").is_file()
    api_key_configured = bool(settings.gemini_api_key.get_secret_value().strip())
    tg_token_configured = bool(settings.telegram_token.get_secret_value().strip())

    profiles_seeded = 0
    db_ok = True
    db_error: str | None = None
    if do_init_db:
        try:
            profiles_seeded = asyncio.run(initialize_database())
        except Exception as exc:
            db_ok = False
            db_error = str(exc)

    all_critical_ok = (
        py_ok
        and bool(ffmpeg_info["available"])
        and bool(ffprobe_info["available"])
        and ytdlp_ok
        and db_ok
    )

    return {
        "ok": all_critical_ok,
        "python": {
            "version": sys.version.split()[0],
            "ok": py_ok,
        },
        "ffmpeg": ffmpeg_info,
        "ffprobe": ffprobe_info,
        "nvenc_available": nvenc_ok,
        "yt_dlp_available": ytdlp_ok,
        "env_file_exists": env_file_exists,
        "gemini_api_key_set": api_key_configured,
        "telegram_token_set": tg_token_configured,
        "capcut_drafts_dir_exists": capcut_root.exists(),
        "capcut_drafts_dir": str(capcut_root),
        "data_dirs_created": created_dirs,
        "database": {
            "initialized": do_init_db and db_ok,
            "profiles_count": profiles_seeded,
            "error": db_error,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify ClipHunter runtime dependencies and initialize storage/database."
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize database schema and seed default streamer profiles",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output diagnostic report as JSON",
    )
    args = parser.parse_args(argv)

    report = run_environment_checks(do_init_db=args.init_db)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["ok"] else 1

    print("=== ClipHunter Environment Check ===")
    print(
        f"Python {report['python']['version']}: {'OK' if report['python']['ok'] else 'FAIL (<3.12)'}"
    )
    print(
        f"FFmpeg:  {'OK' if report['ffmpeg']['available'] else 'MISSING'} ({report['ffmpeg']['version'] or 'not found'})"
    )
    print(f"FFprobe: {'OK' if report['ffprobe']['available'] else 'MISSING'}")
    print(
        f"NVENC hardware encoding: {'Available' if report['nvenc_available'] else 'Not detected (CPU fallback)'}"
    )
    print(f"yt-dlp module: {'OK' if report['yt_dlp_available'] else 'MISSING'}")
    print(
        f".env file: {'Present' if report['env_file_exists'] else 'Missing (copy .env.example to .env)'}"
    )
    print(
        f"GEMINI_API_KEY: {'Configured' if report['gemini_api_key_set'] else 'Not set (required for live LLM calls)'}"
    )
    print(
        f"TELEGRAM_TOKEN: {'Configured' if report['telegram_token_set'] else 'Not set (optional)'}"
    )
    print(
        f"CapCut Desktop drafts dir: {'Found' if report['capcut_drafts_dir_exists'] else 'Not found'}"
    )
    if args.init_db:
        if report["database"]["initialized"]:
            print(
                f"Database initialized: OK ({report['database']['profiles_count']} streamer profiles ready)"
            )
        else:
            print(f"Database initialization FAILED: {report['database']['error']}")
    print("====================================")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
