"""Video layouts and FFmpeg filtergraph builders for horizontal and vertical formats.

Supported layouts (plan, Section 9):
- original: pass-through / 16:9 unchanged
- center_crop_9x16: crop center 9:16 and scale to 1080x1920
- blur_fit_9x16: 16:9 video centered over blurred & cropped background
- facecam_top_game_bottom_9x16: vertical split with facecam on top and gameplay below
"""

from __future__ import annotations

import logging

from cliphunter.models import Profile

log = logging.getLogger(__name__)

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920

DEFAULT_FACECAM_RECT = {"x": 0.70, "y": 0.05, "w": 0.28, "h": 0.35}
DEFAULT_GAMEPLAY_RECT = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}


def build_original_filter() -> str:
    """Pass-through layout without aspect ratio change."""
    return ""


def build_center_crop_9x16_filter(
    target_w: int = TARGET_WIDTH, target_h: int = TARGET_HEIGHT
) -> str:
    """Center crop 9:16 and scale to target vertical resolution."""
    return f"crop=ih*9/16:ih,scale={target_w}:{target_h}"


def build_blur_fit_9x16_filter(target_w: int = TARGET_WIDTH, target_h: int = TARGET_HEIGHT) -> str:
    """Source video centered over scaled and blurred background."""
    return (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},boxblur=30:5[b];"
        f"[fg]scale={target_w}:-2[f];"
        f"[b][f]overlay=(W-w)/2:(H-h)/2"
    )


def build_split_facecam_game_filter(
    profile: Profile | None = None,
    target_w: int = TARGET_WIDTH,
    target_h: int = TARGET_HEIGHT,
    facecam_h: int = 640,
) -> str:
    """Vertical split with facecam on top and gameplay below.

    Cuts regions based on profile ratios (x, y, w, h in 0.0..1.0) and stacks vertically.
    """
    game_h = target_h - facecam_h  # 1920 - 640 = 1280

    fc = (
        profile.facecam_rect if profile and profile.facecam_rect else None
    ) or DEFAULT_FACECAM_RECT
    gp = (
        profile.gameplay_rect if profile and profile.gameplay_rect else None
    ) or DEFAULT_GAMEPLAY_RECT

    fc_x, fc_y = fc.get("x", 0.70), fc.get("y", 0.05)
    fc_w, fc_h = fc.get("w", 0.28), fc.get("h", 0.35)

    gp_x, gp_y = gp.get("x", 0.0), gp.get("y", 0.0)
    gp_w, gp_h = gp.get("w", 1.0), gp.get("h", 1.0)

    # Filtergraph:
    # 1. Split into [fc_in] and [gp_in]
    # 2. Crop facecam region, scale to target_w:facecam_h with force_original_aspect_ratio and crop
    # 3. Crop gameplay region, scale to target_w:game_h with force_original_aspect_ratio and crop
    # 4. vstack top + bottom into [v_out]
    filtergraph = (
        f"[0:v]split=2[fc_raw][gp_raw];"
        f"[fc_raw]crop=w={fc_w:.3f}*iw:h={fc_h:.3f}*ih:x={fc_x:.3f}*iw:y={fc_y:.3f}*ih,"
        f"scale={target_w}:{facecam_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{facecam_h}[fc_out];"
        f"[gp_raw]crop=w={gp_w:.3f}*iw:h={gp_h:.3f}*ih:x={gp_x:.3f}*iw:y={gp_y:.3f}*ih,"
        f"scale={target_w}:{game_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{game_h}[gp_out];"
        f"[fc_out][gp_out]vstack=inputs=2"
    )
    return filtergraph


def get_layout_filter(
    layout: str,
    profile: Profile | None = None,
    target_w: int = TARGET_WIDTH,
    target_h: int = TARGET_HEIGHT,
) -> str:
    """Returns the FFmpeg video filtergraph corresponding to layout name."""
    layout_clean = layout.strip().lower()

    if layout_clean in ("original", "16:9", "16x9", "direct"):
        return build_original_filter()

    if layout_clean in ("center_crop", "center_crop_9x16", "crop_9x16"):
        return build_center_crop_9x16_filter(target_w, target_h)

    if layout_clean in ("blur_fit", "blur_fit_9x16", "blur_background", "blur"):
        return build_blur_fit_9x16_filter(target_w, target_h)

    if layout_clean in (
        "facecam_top_game_bottom_9x16",
        "split_9x16",
        "facecam_gameplay",
        "streamer_split",
    ):
        return build_split_facecam_game_filter(profile, target_w, target_h)

    log.warning("Unknown layout '%s', defaulting to original 16:9", layout)
    return build_original_filter()
