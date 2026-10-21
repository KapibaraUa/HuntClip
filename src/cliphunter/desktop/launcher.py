"""Desktop application window launcher and lifecycle manager."""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from cliphunter.desktop.shortcut import create_desktop_shortcut

log = logging.getLogger(__name__)


def find_free_port(start_port: int = 8000, max_tries: int = 25) -> int:
    """Finds an available local port starting from `start_port`."""
    for port in range(start_port, start_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start_port


def find_app_browser() -> str | None:
    """Locates a Chromium-based browser (MS Edge or Google Chrome) that supports --app mode."""
    candidates = [
        # Microsoft Edge (Standard Windows location)
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        shutil.which("msedge.exe"),
        shutil.which("msedge"),
        # Google Chrome
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        shutil.which("chrome.exe"),
        shutil.which("chrome"),
        # Brave
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    ]

    for c in candidates:
        if c and Path(c).is_file():
            return str(Path(c).resolve())

    return None


def build_app_window_command(
    url: str,
    width: int = 1360,
    height: int = 880,
    user_data_dir: Path | None = None,
) -> list[str] | None:
    """Builds process execution command for launching an isolated standalone app window."""
    browser_exe = find_app_browser()
    if not browser_exe:
        return None

    cmd = [
        browser_exe,
        f"--app={url}",
        f"--window-size={width},{height}",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    if user_data_dir is not None:
        user_data_dir.mkdir(parents=True, exist_ok=True)
        cmd.append(f"--user-data-dir={user_data_dir.resolve().as_posix()}")

    return cmd


def wait_for_server(url: str, timeout_s: float = 15.0) -> bool:
    """Polls a URL until it responds with HTTP 200 or timeout expires."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ClipHunter-Desktop-Probe"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def run_desktop(
    host: str = "127.0.0.1",
    port: int | None = None,
    install_shortcut: bool = False,
    project_root: Path | None = None,
) -> int:
    """Launches ClipHunter as a standalone desktop application window."""
    root = project_root or Path.cwd()

    if install_shortcut:
        try:
            create_desktop_shortcut(root)
        except Exception as exc:
            log.warning("Could not create desktop shortcut: %s", exc)

    chosen_port = port or find_free_port(8000)
    app_url = f"http://{host}:{chosen_port}/?mode=desktop"
    health_url = f"http://{host}:{chosen_port}/api/system/settings"

    import uvicorn

    from cliphunter.api.app import app

    config = uvicorn.Config(
        app=app,
        host=host,
        port=chosen_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config=config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    log.info("Starting background server at %s ...", health_url)
    if not wait_for_server(health_url, timeout_s=15.0):
        print(f"Error: Server at {health_url} failed to start within 15 seconds.", file=sys.stderr)
        server.should_exit = True
        return 1

    profile_dir = root / "data" / "desktop_profile"
    cmd = build_app_window_command(app_url, width=1380, height=890, user_data_dir=profile_dir)

    print(f"ClipHunter Desktop running at {app_url}")
    print("Close the application window to stop the service.")

    if cmd:
        try:
            proc = subprocess.Popen(cmd)
            proc.wait()
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            log.warning("Failed to run app window process: %s. Falling back to browser.", exc)
            webbrowser.open(app_url)
    else:
        log.info("No Chromium browser found for --app mode. Opening in default web browser.")
        webbrowser.open(app_url)
        try:
            while server_thread.is_alive():
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass

    # Graceful shutdown
    print("\nStopping ClipHunter Desktop server...")
    server.should_exit = True
    server_thread.join(timeout=5.0)
    print("ClipHunter Desktop stopped cleanly.")
    return 0
