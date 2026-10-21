"""Desktop shortcut and icon generation utilities for ClipHunter on Windows."""

from __future__ import annotations

import contextlib
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


def generate_desktop_icon(icon_path: Path, force: bool = False) -> Path:
    """Generates a high-quality Windows .ico icon file in CapCut monochrome style."""
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    if not force and icon_path.exists() and icon_path.stat().st_size > 0:
        return icon_path

    # PowerShell script creating a 256x256 modern CapCut-style monochrome app icon
    ps_script = f"""
Add-Type -AssemblyName System.Drawing
$size = 256
$bmp = New-Object System.Drawing.Bitmap $size, $size
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

$g.Clear([System.Drawing.Color]::Transparent)

# Deep black squircle background (CapCut style)
$rect = New-Object System.Drawing.Rectangle 12, 12, 232, 232
$bgBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 10, 10, 12))
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$d = 56
$r = New-Object System.Drawing.Rectangle $rect.X, $rect.Y, $d, $d
$path.AddArc($r, 180, 90)
$r.X = $rect.Right - $d
$path.AddArc($r, 270, 90)
$r.Y = $rect.Bottom - $d
$path.AddArc($r, 0, 90)
$r.X = $rect.Left
$path.AddArc($r, 90, 90)
$path.CloseFigure()
$g.FillPath($bgBrush, $path)

# Subtle silver rim
$rimPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(60, 255, 255, 255), 1.5)
$g.DrawPath($rimPen, $path)

# Target circle (radar)
$circlePen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(35, 255, 255, 255), 1.5)
$circlePen.DashStyle = [System.Drawing.Drawing2D.DashStyle]::Dash
$g.DrawEllipse($circlePen, 78, 78, 100, 100)

# Precision ticks
$tickPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 255, 255, 255), 2.0)
$g.DrawLine($tickPen, 128, 40, 128, 54)
$g.DrawLine($tickPen, 128, 202, 128, 216)
$g.DrawLine($tickPen, 40, 128, 54, 128)
$g.DrawLine($tickPen, 202, 128, 216, 128)

# Bold C ribbon (pure white)
$cPen = New-Object System.Drawing.Pen([System.Drawing.Color]::White, 14.0)
$cPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
$cPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
$g.DrawArc($cPen, 72, 72, 112, 112, 50, 260)

# Play triangle (pure white)
$playPts = @(
    (New-Object System.Drawing.PointF 116, 90),
    (New-Object System.Drawing.PointF 116, 166),
    (New-Object System.Drawing.PointF 178, 128)
)
$whiteBrush = [System.Drawing.Brushes]::White
$g.FillPolygon($whiteBrush, $playPts)

# Black gap outline for Play triangle
$gapPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 10, 10, 12), 3.0)
$g.DrawPolygon($gapPen, $playPts)

# Focal dot at tip of Play triangle
$dotBrush = [System.Drawing.Brushes]::White
$g.FillEllipse($dotBrush, 172, 122, 12, 12)
$innerDot = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 10, 10, 12))
$g.FillEllipse($innerDot, 176, 126, 4, 4)

$g.Dispose()

# Save ICO
$hIcon = $bmp.GetHicon()
$icon = [System.Drawing.Icon]::FromHandle($hIcon)
$fs = [System.IO.File]::Create('{icon_path.as_posix()}')
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
    icon_path = assets_dir / "cliphunter.ico"
    generate_desktop_icon(icon_path)
    # Also keep icon.ico in sync
    with contextlib.suppress(Exception):
        (assets_dir / "icon.ico").write_bytes(icon_path.read_bytes())

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

    # Unlink existing shortcut to ensure clean cache refresh
    if shortcut_path.exists():
        with contextlib.suppress(Exception):
            shortcut_path.unlink()

    icon_win = str(icon_path.resolve())
    shortcut_win = str(shortcut_path.resolve())
    vbs_win = str(vbs_path.resolve())
    root_win = str(project_root.resolve())

    # Generate shortcut via PowerShell WScript.Shell COM using native Windows backslashes
    ps_shortcut = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{shortcut_win}')
$Shortcut.TargetPath = 'wscript.exe'
$Shortcut.Arguments = '"{vbs_win}"'
$Shortcut.WorkingDirectory = '{root_win}'
$Shortcut.Description = 'ClipHunter - ИИ нарезчик хайлайтов и клипов со стримов'
if (Test-Path '{icon_win}') {{
    $Shortcut.IconLocation = '{icon_win},0'
}}
$Shortcut.Save()
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_shortcut],
        check=True,
        capture_output=True,
        timeout=10,
    )

    # Invalidate Windows Shell icon cache so Explorer updates icon immediately
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except Exception:
        pass

    log.info("Created desktop shortcut at %s", shortcut_path)
    return shortcut_path
