"""Unit tests for ClipHunter desktop launcher and shortcut tools."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from cliphunter.api.app import app
from cliphunter.desktop.launcher import build_app_window_command, find_app_browser, find_free_port
from cliphunter.desktop.shortcut import get_desktop_dir


def test_find_free_port() -> None:
    port = find_free_port(start_port=8050)
    assert 8050 <= port < 8080


def test_build_app_window_command(tmp_path: Path) -> None:
    cmd = build_app_window_command(
        url="http://127.0.0.1:8000/?mode=desktop",
        width=1280,
        height=720,
        user_data_dir=tmp_path / "profile",
    )
    if find_app_browser() is not None:
        assert cmd is not None
        assert any(arg.startswith("--app=http://127.0.0.1:8000") for arg in cmd)
        assert "--window-size=1280,720" in cmd
        assert any(arg.startswith("--user-data-dir=") for arg in cmd)
    else:
        # On headless machines without Edge or Chrome
        assert cmd is None


def test_get_desktop_dir() -> None:
    desktop = get_desktop_dir()
    assert desktop is not None
    assert isinstance(desktop, Path)


@pytest.mark.asyncio
async def test_open_folder_api_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Mock os.startfile on Windows or subprocess.run on other platforms
        with (
            patch("os.startfile", create=True) as mock_startfile,
            patch("subprocess.run") as mock_subproc,
        ):
            res = await client.post("/api/system/open-folder", json={"target": "out"})
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ok"
            assert data["target"] == "out"
            assert Path(data["path"]).exists()

            if sys.platform == "win32":
                mock_startfile.assert_called_once()
            else:
                mock_subproc.assert_called_once()

            # Invalid folder target
            bad_res = await client.post(
                "/api/system/open-folder", json={"target": "invalid_folder"}
            )
            assert bad_res.status_code == 422 or bad_res.status_code == 400
