"""Branding, title hook overlays, and template decoration for rendered clips."""

from __future__ import annotations

from pathlib import Path


def get_default_font_file() -> str | None:
    """Finds available system TTF font to avoid fontconfig issues across platforms."""
    candidates = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
    ]
    for p in candidates:
        if p.is_file():
            # In FFmpeg filter parameters, backslashes must be forward slashes and colons escaped: C\:/...
            formatted = str(p.resolve()).replace("\\", "/")
            return formatted.replace(":", "\\:")
    return None


def escape_drawtext(text: str) -> str:
    """Escapes characters for FFmpeg drawtext filter parameters."""
    if not text:
        return ""
    # In FFmpeg drawtext, colons, single quotes, backslashes and percents must be escaped
    escaped = (
        text.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:").replace("%", "\\%")
    )
    return escaped


def build_title_hook_filter(
    title: str,
    start_t: float = 0.2,
    end_t: float = 3.8,
    font_size: int = 44,
    y_fraction: float = 0.12,
    font_path: str | None = None,
) -> str:
    """Creates a drawtext filter overlay displaying the hook title in the first seconds."""
    clean_title = escape_drawtext(title.strip())
    if not clean_title:
        return ""

    font_file = font_path or get_default_font_file()
    font_param = f"fontfile='{font_file}':" if font_file else ""

    # Semi-transparent pill background with white bold text
    filter_str = (
        f"drawtext={font_param}"
        f"text='{clean_title}':"
        f"x=(w-text_w)/2:y=h*{y_fraction:.2f}:"
        f"fontsize={font_size}:fontcolor=white:"
        f"box=1:boxcolor=black@0.65:boxborderw=12:"
        f"line_spacing=8:"
        f"enable='between(t,{start_t:.2f},{end_t:.2f})'"
    )
    return filter_str


def build_watermark_filter(watermark_path: Path | str, padding: int = 24) -> str:
    """Builds an overlay filter for streamer logo or watermark in top-right corner."""
    wm = Path(watermark_path)
    if not wm.is_file():
        return ""

    escaped_path = str(wm).replace("\\", "/").replace(":", "\\:")
    # Scale watermark to max width 160px and overlay at top-right
    return f"movie='{escaped_path}',scale=160:-1[wm];[in][wm]overlay=W-w-{padding}:{padding}"
