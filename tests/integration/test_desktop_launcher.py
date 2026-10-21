"""Integration tests for desktop server polling and shortcut generation."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from cliphunter.desktop.launcher import wait_for_server
from cliphunter.desktop.shortcut import create_desktop_shortcut


class _PingHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format: str, *args: object) -> None:
        pass


def test_wait_for_server_healthy() -> None:
    server = HTTPServer(("127.0.0.1", 0), _PingHandler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/"
    healthy = wait_for_server(url, timeout_s=3.0)
    server.shutdown()
    assert healthy is True


def test_create_desktop_shortcut_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point Desktop to tmp_path
    monkeypatch.setattr("cliphunter.desktop.shortcut.get_desktop_dir", lambda: tmp_path / "Desktop")
    shortcut = create_desktop_shortcut(tmp_path)
    assert shortcut.name == "ClipHunter.lnk"
    assert (tmp_path / "ClipHunter.bat").exists()
    assert (tmp_path / "scripts" / "launch_desktop.vbs").exists()
