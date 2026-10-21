"""Desktop shortcut and icon generation utilities for ClipHunter on Windows."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def get_desktop_dir() -> Path:
    """Returns the user's Windows Desktop directory path."""
    desktop_env = os.environ.get("USERPROFILE")
    if desktop_env:
        p = Path(desktop_env) / "Desktop"
        if p.exists():
            return p

    # Fallback via PowerShell
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = proc.stdout.strip()
        if out and Path(out).exists():
            return Path(out)
    except Exception as exc:
        log.warning("Could not query Desktop path via PowerShell: %s", exc)

    return Path.home() / "Desktop"


def generate_desktop_icon(icon_path: Path) -> Path:
    """Generates a high-quality Windows .ico icon file using System.Drawing if available."""
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    if icon_path.exists() and icon_path.stat().st_size > 0:
        return icon_path

    # PowerShell script creating a 256x256 modern gradient app icon
    ps_script = f"""
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap 256, 256
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias

# Background rounded rect
$rect = New-Object System.Drawing.Rectangle 16, 16, 224, 224
$brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
    $rect,
    [System.Drawing.Color]::FromArgb(255, 99, 102, 241),
    [System.Drawing.Color]::FromArgb(255, 139, 92, 246),
    45.0
)
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$d = 48
$r = [System.Drawing.Rectangle]::new($rect.X, $rect.Y, $d, $d)
$path.AddArc($r, 180, 90)
$r.X = $rect.Right - $d
$path.AddArc($r, 270, 90)
$r.Y = $rect.Bottom - $d
$path.AddArc($r, 0, 90)
$r.X = $rect.Left
$path.AddArc($r, 90, 90)
$path.CloseFigure()
$g.FillPath($brush, $path)

# Play triangle (ClipHunter mark)
$pts = @(
    New-Object System.Drawing.PointF 100, 75
    New-Object System.Drawing.PointF 100, 181
    New-Object System.Drawing.PointF 185, 128
)
$whiteBrush = [System.Drawing.Brushes]::White
$g.FillPolygon($whiteBrush, $pts)

$g.Dispose()

# Save as ICO
$hIcon = $bmp.GetHicon()
$icon = [System.Drawing.Icon]::FromHandle($hIcon)
$fs = [System.IO.File]::OpenWrite('{icon_path.as_posix()}')
$icon.Save($fs)
$fs.Close()
$icon.Dispose()
$bmp.Dispose()
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except Exception as exc:
        log.warning("Could not generate .ico via System.Drawing: %s. Creating basic stub.", exc)
        # Fallback: create empty placeholder or let Windows use default executable icon
        icon_path.write_bytes(b"")

    return icon_path


def create_desktop_shortcut(
    project_root: Path,
    shortcut_name: str = "ClipHunter.lnk",
) -> Path:
    """Creates a Windows Desktop shortcut (.lnk) that launches the ClipHunter desktop app."""
    desktop_dir = get_desktop_dir()
    desktop_dir.mkdir(parents=True, exist_ok=True)
    shortcut_path = desktop_dir / shortcut_name

    assets_dir = project_root / "assets"
    icon_path = assets_dir / "icon.ico"
    generate_desktop_icon(icon_path)

    # Launcher batch file
    bat_path = project_root / "ClipHunter.bat"
    bat_content = f"""@echo off
cd /d "{project_root}"
uv run cliphunter desktop
"""
    bat_path.write_text(bat_content, encoding="utf-8")

    # Launcher VBScript (silent launch without flashing black CMD console)
    vbs_path = project_root / "scripts" / "launch_desktop.vbs"
    vbs_path.parent.mkdir(parents=True, exist_ok=True)
    vbs_content = f"""Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{project_root}"
WshShell.Run "uv run cliphunter desktop", 0, False
"""
    vbs_path.write_text(vbs_content, encoding="utf-8")

    # Generate shortcut via PowerShell WScript.Shell COM
    ps_shortcut = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{shortcut_path.as_posix()}')
$Shortcut.TargetPath = 'wscript.exe'
$Shortcut.Arguments = '"{vbs_path.as_posix()}"'
$Shortcut.WorkingDirectory = '{project_root.as_posix()}'
$Shortcut.Description = 'ClipHunter - ИИ нарезчик хайлайтов и клипов со стримов'
if (Test-Path '{icon_path.as_posix()}') {{
    $Shortcut.IconLocation = '{icon_path.as_posix()}, 0'
}}
$Shortcut.Save()
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_shortcut],
        check=True,
        capture_output=True,
        timeout=10,
    )

    log.info("Created desktop shortcut at %s", shortcut_path)
    return shortcut_path
