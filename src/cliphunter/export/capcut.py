"""CapCut desktop draft exporter (plan, Sections 10 & 14, Phase 5).

Generates native CapCut Desktop draft directories (`draft_content.json`, `draft_info.json`,
`draft_meta_info.json`, and auxiliary config files) verified against CapCut Desktop Windows
version 9.6.0.4097 (schema version 360000).

No CapCut UI automation is performed: the exporter only writes local draft files into
`out/{job}/capcut_drafts/{clip_id}` which can be copied or installed into CapCut's drafts folder.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from cliphunter.models import ClipManifest
from cliphunter.video.ffmpeg import probe_cmd, run
from cliphunter.video.subtitles import SubtitleWord

log = logging.getLogger(__name__)

# Verified CapCut Desktop version on Windows (recorded in docs/DECISIONS.md)
CAPCUT_VERIFIED_VERSION = "9.6.0.4097"
CAPCUT_APP_VERSION = "9.6.0"
CAPCUT_APP_ID = 359289
CAPCUT_SCHEMA_VERSION = 360000
CAPCUT_NEW_VERSION = "138.0.0"

MICROSECONDS_PER_SEC = 1_000_000


def sec_to_us(seconds: float) -> int:
    """Converts seconds (float) to CapCut microseconds (int)."""
    return max(0, int(round(seconds * MICROSECONDS_PER_SEC)))


def _new_capcut_id() -> str:
    """Generates an uppercase UUID with hyphens matching CapCut 9.6.0 ID format."""
    return str(uuid.uuid4()).upper()


class CapCutClipSpec(BaseModel):
    """Normalized input specification for exporting a single clip to a CapCut draft."""

    clip_id: str
    video_path: Path
    title: str = ""
    duration_s: float = 0.0
    source_start_s: float = 0.0
    source_total_duration_s: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 30.0
    layout: str = "center_crop_9x16"
    template: str = "default"
    title_duration_s: float = 3.5
    subtitles: list[SubtitleWord] = Field(default_factory=list)
    ass_path: Path | None = None
    copy_media: bool = False


@runtime_checkable
class Exporter(Protocol):
    """Protocol for clip exporters (plan, Section 10: `Exporter.export(clip) -> Path`)."""

    def export(self, clip: CapCutClipSpec | ClipManifest | dict[str, Any]) -> Path:
        """Exports a rendered clip into an external editing format and returns its path."""
        ...


def _probe_video_stream_info(video_path: Path) -> tuple[int, int, float, float]:
    """Probes (width, height, duration_s, fps) via ffprobe, with safe fallback defaults."""
    if not video_path.is_file() or video_path.stat().st_size == 0:
        return (0, 0, 0.0, 30.0)
    try:
        proc = run(probe_cmd(video_path), timeout_s=15)
        info = json.loads(proc.stdout)
        streams: list[dict[str, Any]] = info.get("streams", [])
        v_stream: dict[str, Any] = next((s for s in streams if s.get("codec_type") == "video"), {})
        width = int(v_stream.get("width", 0) or 0)
        height = int(v_stream.get("height", 0) or 0)
        dur_str = v_stream.get("duration") or info.get("format", {}).get("duration") or "0"
        duration_s = float(dur_str)

        fps = 30.0
        r_frame_rate = str(v_stream.get("r_frame_rate", "30/1"))
        if "/" in r_frame_rate:
            num_s, den_s = r_frame_rate.split("/", 1)
            num, den = float(num_s), float(den_s)
            if den > 0 and num > 0:
                fps = round(num / den, 2)
        elif r_frame_rate:
            fps = float(r_frame_rate)
        return (width, height, duration_s, fps)
    except Exception as exc:
        log.debug("Could not probe video %s for CapCut metadata: %s", video_path, exc)
        return (0, 0, 0.0, 30.0)


def _parse_ass_timestamp(ts: str) -> float:
    """Parses an ASS timestamp `H:MM:SS.cc` into seconds."""
    parts = ts.strip().split(":")
    if len(parts) != 3:
        return 0.0
    hours = int(parts[0])
    minutes = int(parts[1])
    secs = float(parts[2])
    return hours * 3600.0 + minutes * 60.0 + secs


def parse_ass_subtitles(ass_path: Path) -> list[SubtitleWord]:
    """Extracts timed subtitle phrases from an `.ass` file, deduplicating karaoke word steps."""
    if not ass_path.is_file():
        return []

    raw_entries: list[SubtitleWord] = []
    tag_re = re.compile(r"\{[^}]*\}")

    for line in ass_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        # Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
        fields = line.split(",", 9)
        if len(fields) < 10:
            continue
        start_s = _parse_ass_timestamp(fields[1])
        end_s = _parse_ass_timestamp(fields[2])
        clean_text = tag_re.sub("", fields[9]).replace("\\N", " ").strip()
        if not clean_text or end_s <= start_s:
            continue
        raw_entries.append(SubtitleWord(word=clean_text, start_s=start_s, end_s=end_s))

    # Merge consecutive identical lines (produced by karaoke word-by-word highlighting)
    merged: list[SubtitleWord] = []
    for entry in raw_entries:
        if (
            merged
            and merged[-1].word == entry.word
            and abs(entry.start_s - merged[-1].end_s) < 0.25
        ):
            merged[-1].end_s = max(merged[-1].end_s, entry.end_s)
        else:
            merged.append(entry)

    return merged


def group_subtitle_words(
    words: list[SubtitleWord],
    words_per_line: int = 4,
    max_duration_s: float = 0.0,
) -> list[SubtitleWord]:
    """Groups word-level timestamps into non-overlapping subtitle lines for CapCut."""
    if not words:
        return []

    # Check if entries already look like multi-word lines
    if all(" " in w.word.strip() for w in words):
        lines = [SubtitleWord(word=w.word.strip(), start_s=w.start_s, end_s=w.end_s) for w in words]
    else:
        lines = []
        for i in range(0, len(words), max(1, words_per_line)):
            chunk = words[i : i + max(1, words_per_line)]
            if not chunk:
                continue
            text = " ".join(w.word.strip() for w in chunk if w.word.strip())
            if not text:
                continue
            lines.append(
                SubtitleWord(
                    word=text,
                    start_s=chunk[0].start_s,
                    end_s=chunk[-1].end_s,
                )
            )

    # Ensure strictly non-overlapping segments on the single subtitle track
    sanitized: list[SubtitleWord] = []
    cursor_s = 0.0
    for item in lines:
        start_s = max(cursor_s, item.start_s)
        end_s = item.end_s
        if max_duration_s > 0:
            end_s = min(end_s, max_duration_s)
        if end_s - start_s < 0.05:
            continue
        sanitized.append(SubtitleWord(word=item.word, start_s=start_s, end_s=end_s))
        cursor_s = end_s

    return sanitized


def _build_text_material(
    material_id: str,
    text: str,
    font_size: float = 11.0,
    text_color_rgb: tuple[float, float, float] = (1.0, 1.0, 1.0),
    text_color_hex: str = "#FFFFFF",
    border_width: float = 0.08,
    bold: bool = True,
    background_alpha: float = 0.0,
) -> dict[str, Any]:
    """Builds a CapCut `materials.texts` object with rich-text `content` JSON."""
    strokes: list[dict[str, Any]] = []
    check_flag = 7
    if border_width > 0:
        check_flag |= 8
        strokes.append(
            {
                "content": {
                    "render_type": "solid",
                    "solid": {
                        "alpha": 1.0,
                        "color": [0.0, 0.0, 0.0],
                    },
                },
                "width": border_width,
                "mode": 0,
            }
        )
    if background_alpha > 0:
        check_flag |= 16

    content_payload = {
        "text": text,
        "styles": [
            {
                "fill": {
                    "alpha": 1.0,
                    "content": {
                        "render_type": "solid",
                        "solid": {
                            "alpha": 1.0,
                            "color": list(text_color_rgb),
                        },
                    },
                },
                "range": [0, len(text)],
                "size": font_size,
                "bold": bold,
                "italic": False,
                "underline": False,
                "strokes": strokes,
            }
        ],
    }

    mat: dict[str, Any] = {
        "id": material_id,
        "type": "text",
        "name": "",
        "content": json.dumps(content_payload, ensure_ascii=False),
        "base_content": "",
        "alignment": 1,
        "typesetting": 0,
        "letter_spacing": 0.0,
        "line_spacing": 0.02,
        "line_feed": 1,
        "line_max_width": 0.82,
        "force_apply_line_max_width": False,
        "check_flag": check_flag,
        "global_alpha": 1.0,
        "font_size": font_size,
        "font_path": "C:/Windows/Fonts/arial.ttf",
        "font_id": "",
        "font_name": "Arial",
        "font_title": "none",
        "text_color": text_color_hex,
        "text_alpha": 1.0,
        "border_alpha": 1.0,
        "border_color": "#000000",
        "border_width": border_width,
        "border_mode": 0,
        "has_shadow": False,
        "shadow_alpha": 0.9,
        "shadow_angle": -45.0,
        "shadow_color": "#000000",
        "shadow_distance": 5.0,
        "shadow_smoothing": 0.45,
        "shadow_point": {"x": 0.6363961030678928, "y": -0.6363961030678928},
        "fixed_width": -1,
        "fixed_height": -1,
        "is_rich_text": True,
        "words": {"start_time": [], "end_time": [], "text": []},
        "current_words": {"start_time": [], "end_time": [], "text": []},
    }
    if background_alpha > 0:
        mat.update(
            {
                "background_style": 1,
                "background_color": "#000000",
                "background_alpha": background_alpha,
                "background_round_radius": 0.15,
                "background_height": 0.14,
                "background_width": 0.14,
                "background_horizontal_offset": 0.0,
                "background_vertical_offset": 0.0,
            }
        )
    return mat


def _build_text_segment(
    material_id: str,
    start_us: int,
    duration_us: int,
    transform_y: float = 0.65,
    render_index: int = 15000,
    track_render_index: int = 1,
) -> dict[str, Any]:
    """Builds a CapCut text segment placed on a text track."""
    return {
        "id": _new_capcut_id(),
        "material_id": material_id,
        "source_timerange": None,
        "target_timerange": {
            "start": start_us,
            "duration": duration_us,
        },
        "render_timerange": {
            "start": 0,
            "duration": 0,
        },
        "desc": "",
        "state": 0,
        "speed": 1.0,
        "is_loop": False,
        "is_tone_modify": False,
        "reverse": False,
        "intensifies_audio": False,
        "cartoon": False,
        "volume": 1.0,
        "last_nonzero_volume": 1.0,
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": transform_y},
        },
        "uniform_scale": {"on": True, "value": 1.0},
        "extra_material_refs": [],
        "render_index": render_index,
        "track_render_index": track_render_index,
        "track_attribute": 0,
        "visible": True,
        "keyframe_refs": [],
        "common_keyframes": [],
        "enable_adjust": False,
        "enable_lut": False,
        "enable_hsl": False,
        "enable_color_curves": True,
        "enable_color_wheels": True,
        "enable_color_match_adjust": False,
        "enable_color_correct_adjust": False,
        "enable_smart_color_adjust": False,
        "hdr_settings": None,
        "group_id": "",
        "is_placeholder": False,
        "template_id": "",
        "template_scene": "default",
        "caption_info": None,
        "responsive_layout": {
            "enable": False,
            "target_follow": "",
            "size_layout": 0,
            "horizontal_pos_layout": 0,
            "vertical_pos_layout": 0,
        },
        "source": "segmentsourcenormal",
    }


class CapCutExporter:
    """Exports a rendered clip (or source clip slice) into a CapCut Desktop draft directory.

    Implements `Exporter.export(clip) -> Path` from Section 10 of the plan.
    Output directory: `{out_dir}/capcut_drafts/{clip_id}`.
    """

    def __init__(self, out_dir: Path, copy_media: bool = False) -> None:
        self.out_dir = Path(out_dir)
        self.copy_media = copy_media

    def _normalize_spec(
        self, clip: CapCutClipSpec | ClipManifest | dict[str, Any]
    ) -> CapCutClipSpec:
        if isinstance(clip, CapCutClipSpec):
            return clip

        if isinstance(clip, ClipManifest):
            title = clip.working_title or (clip.title_options[0] if clip.title_options else "")
            return CapCutClipSpec(
                clip_id=clip.id,
                video_path=Path(clip.output_file),
                title=title,
                duration_s=clip.duration_s,
                source_start_s=0.0,
                layout=clip.layout,
                template=clip.template,
                copy_media=self.copy_media,
            )

        if isinstance(clip, dict):
            clip_id = str(
                clip.get("clip_id")
                or clip.get("id")
                or clip.get("candidate_id")
                or f"clip_{uuid.uuid4().hex[:8]}"
            )
            video_path_str = str(
                clip.get("video_path") or clip.get("output_file") or clip.get("path") or ""
            )
            raw_manifest = clip.get("manifest")
            manifest_dict: dict[str, Any] = raw_manifest if isinstance(raw_manifest, dict) else {}
            title = str(
                clip.get("title")
                or clip.get("working_title")
                or manifest_dict.get("title")
                or manifest_dict.get("working_title")
                or ""
            )
            if not title:
                opts = clip.get("title_options") or manifest_dict.get("title_options") or []
                if opts and isinstance(opts, list):
                    title = str(opts[0])

            duration_s = float(clip.get("duration_s") or manifest_dict.get("duration_s") or 0.0)
            layout = str(clip.get("layout") or manifest_dict.get("layout") or "center_crop_9x16")
            template = str(clip.get("template") or manifest_dict.get("template") or "default")
            source_start_s = float(clip.get("source_start_s") or 0.0)
            ass_raw = clip.get("ass_path")
            ass_path = Path(ass_raw) if ass_raw else None
            subtitles = clip.get("subtitles") or []

            return CapCutClipSpec(
                clip_id=clip_id,
                video_path=Path(video_path_str),
                title=title,
                duration_s=duration_s,
                source_start_s=source_start_s,
                layout=layout,
                template=template,
                ass_path=ass_path,
                subtitles=subtitles,
                copy_media=bool(clip.get("copy_media", self.copy_media)),
            )

        raise TypeError(f"Unsupported clip input type for CapCutExporter: {type(clip)}")

    def export(self, clip: CapCutClipSpec | ClipManifest | dict[str, Any]) -> Path:
        """Creates a complete CapCut draft folder at `out_dir/capcut_drafts/{clip_id}`."""
        spec = self._normalize_spec(clip)
        draft_dir = self.out_dir / "capcut_drafts" / spec.clip_id
        draft_dir.mkdir(parents=True, exist_ok=True)

        # Probe video file if it exists
        probed_w, probed_h, probed_dur_s, probed_fps = _probe_video_stream_info(spec.video_path)

        is_vertical_layout = (
            "9x16" in spec.layout or "split" in spec.layout or "blur" in spec.layout
        )
        default_w = 1080 if is_vertical_layout else 1920
        default_h = 1920 if is_vertical_layout else 1080

        width = spec.width or probed_w or default_w
        height = spec.height or probed_h or default_h
        fps = spec.fps if spec.fps != 30.0 else (probed_fps or 30.0)
        duration_s = spec.duration_s if spec.duration_s > 0 else (probed_dur_s or 15.0)
        source_total_s = (
            spec.source_total_duration_s
            if spec.source_total_duration_s > 0
            else max(probed_dur_s, spec.source_start_s + duration_s)
        )

        # Optionally copy video asset into draft folder for portability
        effective_video_path = spec.video_path.resolve()
        if (spec.copy_media or self.copy_media) and spec.video_path.is_file():
            media_dir = draft_dir / "Resources" / "video"
            media_dir.mkdir(parents=True, exist_ok=True)
            copied_video = media_dir / spec.video_path.name
            if copied_video.resolve() != spec.video_path.resolve():
                shutil.copy2(spec.video_path, copied_video)
            effective_video_path = copied_video.resolve()

        video_posix_path = effective_video_path.as_posix()
        material_name = effective_video_path.name or f"{spec.clip_id}.mp4"
        file_size_bytes = (
            effective_video_path.stat().st_size if effective_video_path.is_file() else 0
        )

        duration_us = sec_to_us(duration_s)
        source_start_us = sec_to_us(spec.source_start_s)
        source_total_us = max(duration_us, sec_to_us(source_total_s))
        now_sec = int(time.time())
        now_us = int(time.time() * MICROSECONDS_PER_SEC)

        draft_uuid = _new_capcut_id()
        video_mat_id = _new_capcut_id()
        speed_mat_id = _new_capcut_id()
        canvas_mat_id = _new_capcut_id()
        sound_map_id = _new_capcut_id()
        vocal_sep_id = _new_capcut_id()
        video_track_id = _new_capcut_id()
        video_seg_id = _new_capcut_id()

        ratio = "9:16" if width <= height else "16:9"

        # 1. Video material
        video_material: dict[str, Any] = {
            "id": video_mat_id,
            "material_id": video_mat_id,
            "local_material_id": "",
            "type": "video",
            "path": video_posix_path,
            "media_path": "",
            "material_name": material_name,
            "material_url": "",
            "category_id": "",
            "category_name": "local",
            "check_flag": 63487,
            "duration": source_total_us,
            "width": width,
            "height": height,
            "has_audio": True,
            "audio_fade": None,
            "crop": {
                "upper_left_x": 0.0,
                "upper_left_y": 0.0,
                "upper_right_x": 1.0,
                "upper_right_y": 0.0,
                "lower_left_x": 0.0,
                "lower_left_y": 1.0,
                "lower_right_x": 1.0,
                "lower_right_y": 1.0,
            },
            "crop_ratio": "free",
            "crop_scale": 1.0,
            "extra_type_option": 0,
            "source": 0,
            "source_platform": 0,
        }

        speed_material: dict[str, Any] = {
            "id": speed_mat_id,
            "mode": 0,
            "speed": 1.0,
            "curve_speed": None,
            "type": "speed",
        }

        canvas_material: dict[str, Any] = {
            "id": canvas_mat_id,
            "type": "canvas_color",
            "color": "",
            "blur": 0.0,
            "image": "",
            "image_id": "",
            "image_name": "",
            "source_platform": 0,
            "team_id": "",
            "album_image": "",
        }

        sound_channel_mapping: dict[str, Any] = {
            "id": sound_map_id,
            "type": "",
            "audio_channel_mapping": 0,
            "is_config_open": False,
        }

        vocal_separation: dict[str, Any] = {
            "id": vocal_sep_id,
            "type": "vocal_separation",
            "choice": 0,
            "production_path": "",
            "time_range": None,
            "removed_sounds": [],
        }

        # 2. Video segment & track
        video_segment: dict[str, Any] = {
            "id": video_seg_id,
            "material_id": video_mat_id,
            "source_timerange": {
                "start": source_start_us,
                "duration": duration_us,
            },
            "target_timerange": {
                "start": 0,
                "duration": duration_us,
            },
            "render_timerange": {
                "start": 0,
                "duration": 0,
            },
            "desc": "",
            "state": 0,
            "speed": 1.0,
            "is_loop": False,
            "is_tone_modify": False,
            "reverse": False,
            "intensifies_audio": False,
            "cartoon": False,
            "volume": 1.0,
            "last_nonzero_volume": 1.0,
            "clip": {
                "alpha": 1.0,
                "flip": {"horizontal": False, "vertical": False},
                "rotation": 0.0,
                "scale": {"x": 1.0, "y": 1.0},
                "transform": {"x": 0.0, "y": 0.0},
            },
            "uniform_scale": {"on": True, "value": 1.0},
            "extra_material_refs": [
                speed_mat_id,
                canvas_mat_id,
                sound_map_id,
                vocal_sep_id,
            ],
            "render_index": 0,
            "track_render_index": 0,
            "track_attribute": 0,
            "visible": True,
            "keyframe_refs": [],
            "common_keyframes": [],
            "enable_adjust": True,
            "enable_lut": True,
            "enable_hsl": False,
            "enable_color_curves": True,
            "enable_color_wheels": True,
            "enable_color_match_adjust": False,
            "enable_color_correct_adjust": False,
            "enable_smart_color_adjust": False,
            "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
            "group_id": "",
            "is_placeholder": False,
            "template_id": "",
            "template_scene": "default",
            "source": "segmentsourcenormal",
        }

        tracks: list[dict[str, Any]] = [
            {
                "id": video_track_id,
                "type": "video",
                "name": "video_main",
                "is_default_name": False,
                "attribute": 0,
                "flag": 0,
                "segments": [video_segment],
            }
        ]

        texts_materials: list[dict[str, Any]] = []

        # 3. Title hook text track
        title_text = spec.title.strip()
        if title_text:
            title_mat_id = _new_capcut_id()
            title_dur_us = min(duration_us, max(500_000, sec_to_us(spec.title_duration_s)))
            texts_materials.append(
                _build_text_material(
                    material_id=title_mat_id,
                    text=title_text,
                    font_size=11.0,
                    text_color_rgb=(1.0, 1.0, 1.0),
                    text_color_hex="#FFFFFF",
                    border_width=0.08,
                    bold=True,
                    background_alpha=0.55,
                )
            )
            title_seg = _build_text_segment(
                material_id=title_mat_id,
                start_us=0,
                duration_us=title_dur_us,
                transform_y=0.65,
                render_index=15000,
                track_render_index=1,
            )
            tracks.append(
                {
                    "id": _new_capcut_id(),
                    "type": "text",
                    "name": "title_hook",
                    "is_default_name": False,
                    "attribute": 0,
                    "flag": 0,
                    "segments": [title_seg],
                }
            )

        # 4. Optional Subtitles track
        raw_subs = list(spec.subtitles)
        if not raw_subs and spec.ass_path and spec.ass_path.is_file():
            raw_subs = parse_ass_subtitles(spec.ass_path)

        grouped_subs = group_subtitle_words(raw_subs, words_per_line=4, max_duration_s=duration_s)
        if grouped_subs:
            sub_segments: list[dict[str, Any]] = []
            for sub in grouped_subs:
                sub_mat_id = _new_capcut_id()
                sub_start_us = sec_to_us(sub.start_s)
                sub_dur_us = max(50_000, sec_to_us(sub.end_s - sub.start_s))
                texts_materials.append(
                    _build_text_material(
                        material_id=sub_mat_id,
                        text=sub.word,
                        font_size=9.0,
                        text_color_rgb=(1.0, 0.94, 0.0),
                        text_color_hex="#FFF000",
                        border_width=0.08,
                        bold=True,
                        background_alpha=0.0,
                    )
                )
                sub_segments.append(
                    _build_text_segment(
                        material_id=sub_mat_id,
                        start_us=sub_start_us,
                        duration_us=sub_dur_us,
                        transform_y=-0.75,
                        render_index=15001,
                        track_render_index=2,
                    )
                )
            tracks.append(
                {
                    "id": _new_capcut_id(),
                    "type": "text",
                    "name": "subtitles",
                    "is_default_name": False,
                    "attribute": 0,
                    "flag": 0,
                    "segments": sub_segments,
                }
            )

        platform_info: dict[str, Any] = {
            "app_id": CAPCUT_APP_ID,
            "app_source": "cc",
            "app_version": CAPCUT_APP_VERSION,
            "device_id": "cliphunter_exporter",
            "hard_disk_id": "",
            "mac_address": "",
            "os": "windows",
            "os_version": "10.0.26200",
        }

        draft_content: dict[str, Any] = {
            "id": draft_uuid,
            "version": CAPCUT_SCHEMA_VERSION,
            "new_version": CAPCUT_NEW_VERSION,
            "name": spec.clip_id,
            "duration": duration_us,
            "create_time": now_sec,
            "update_time": now_sec,
            "fps": float(fps),
            "is_drop_frame_timecode": False,
            "color_space": 0,
            "config": {
                "adjust_max_index": 1,
                "attachment_info": [],
                "combination_max_index": 1,
                "export_range": None,
                "extract_audio_last_index": 1,
                "lyrics_recognition_id": "",
                "lyrics_sync": True,
                "lyrics_taskinfo": [],
                "maintrack_adsorb": True,
                "material_save_mode": 0,
                "multi_language_current": "none",
                "multi_language_list": [],
                "multi_language_main": "none",
                "multi_language_mode": "none",
                "original_sound_last_index": 1,
                "record_audio_last_index": 1,
                "sticker_max_index": 1,
                "subtitle_keywords_config": None,
                "subtitle_recognition_id": "",
                "subtitle_sync": True,
                "subtitle_taskinfo": [],
                "system_font_list": [],
                "use_float_render": False,
                "video_mute": False,
                "zoom_info_params": None,
            },
            "canvas_config": {
                "width": width,
                "height": height,
                "ratio": ratio,
                "background": None,
            },
            "cover": None,
            "retouch_cover": None,
            "extra_info": None,
            "group_container": None,
            "free_render_index_mode_on": False,
            "render_index_track_mode_on": True,
            "mixed_track_mode_on": False,
            "keyframe_graph_list": [],
            "keyframes": {
                "adjusts": [],
                "audios": [],
                "effects": [],
                "filters": [],
                "handwrites": [],
                "stickers": [],
                "texts": [],
                "videos": [],
            },
            "platform": platform_info,
            "last_modified_platform": platform_info,
            "lyrics_effects": [],
            "materials": {
                "ai_translates": [],
                "audio_balances": [],
                "audio_effects": [],
                "audio_fades": [],
                "audio_pannings": [],
                "audio_pitch_shifts": [],
                "audio_track_indexes": [],
                "audios": [],
                "beats": [],
                "canvases": [canvas_material],
                "chromas": [],
                "color_curves": [],
                "common_mask": [],
                "digital_human_model_dressing": [],
                "digital_humans": [],
                "drafts": [],
                "effects": [],
                "flowers": [],
                "green_screens": [],
                "handwrites": [],
                "hsl": [],
                "hsl_curves": [],
                "images": [],
                "log_color_wheels": [],
                "loudnesses": [],
                "manual_beautys": [],
                "manual_deformations": [],
                "masks": [],
                "material_animations": [],
                "material_colors": [],
                "multi_language_refs": [],
                "placeholder_infos": [],
                "placeholders": [],
                "plugin_effects": [],
                "primary_color_wheels": [],
                "realtime_denoises": [],
                "shapes": [],
                "smart_crops": [],
                "smart_relights": [],
                "sound_channel_mappings": [sound_channel_mapping],
                "speeds": [speed_material],
                "stickers": [],
                "tail_leaders": [],
                "text_templates": [],
                "texts": texts_materials,
                "time_marks": [],
                "transitions": [],
                "video_effects": [],
                "video_radius": [],
                "video_shadows": [],
                "video_strokes": [],
                "video_trackings": [],
                "videos": [video_material],
                "vocal_beautifys": [],
                "vocal_separations": [vocal_separation],
            },
            "mutable_config": None,
            "path": "",
            "relationships": [],
            "source": "default",
            "static_cover_image_path": "",
            "time_marks": None,
            "tracks": tracks,
            "uneven_animation_template_info": {
                "composition": "",
                "content": "",
                "order": "",
                "sub_template_info_list": [],
            },
        }

        draft_meta_info: dict[str, Any] = {
            "cloud_draft_cover": False,
            "cloud_draft_sync": False,
            "cloud_package_completed_time": "",
            "draft_cloud_capcut_purchase_info": "",
            "draft_cloud_last_action_download": False,
            "draft_cloud_package_type": "",
            "draft_cloud_purchase_info": "",
            "draft_cloud_template_id": "",
            "draft_cloud_tutorial_info": "",
            "draft_cloud_videocut_purchase_info": "",
            "draft_cover": "draft_cover.jpg",
            "draft_deeplink_url": "",
            "draft_enterprise_info": {
                "draft_enterprise_extra": "",
                "draft_enterprise_id": "",
                "draft_enterprise_name": "",
                "enterprise_material": [],
            },
            "draft_fold_path": draft_dir.resolve().as_posix(),
            "draft_has_unfinished_aigc_video_effect": False,
            "draft_id": draft_uuid,
            "draft_is_ae_produce": False,
            "draft_is_ai_packaging_used": False,
            "draft_is_ai_shorts": False,
            "draft_is_ai_translate": False,
            "draft_is_article_video_draft": False,
            "draft_is_cloud_temp_draft": False,
            "draft_is_from_deeplink": "false",
            "draft_is_infinite_canvas_draft": False,
            "draft_is_invisible": False,
            "draft_is_pippit_draft": False,
            "draft_is_web_article_video": False,
            "draft_materials": [
                {
                    "type": 0,
                    "value": [
                        {
                            "ai_group_type": "",
                            "create_time": now_sec,
                            "duration": source_total_us,
                            "enter_from": 0,
                            "extra_info": material_name,
                            "file_Path": video_posix_path,
                            "height": height,
                            "id": str(uuid.uuid4()),
                            "import_time": now_sec,
                            "import_time_ms": now_us,
                            "item_source": 1,
                            "material_color_tag": "",
                            "md5": "",
                            "metetype": "video",
                            "roughcut_time_range": {
                                "duration": source_total_us,
                                "start": 0,
                            },
                            "sub_time_range": {
                                "duration": -1,
                                "start": -1,
                            },
                            "type": 0,
                            "width": width,
                        }
                    ],
                },
                {"type": 1, "value": []},
                {"type": 2, "value": []},
                {"type": 3, "value": []},
                {"type": 6, "value": []},
                {"type": 7, "value": []},
                {"type": 8, "value": []},
            ],
            "draft_materials_copied_info": [],
            "draft_name": spec.clip_id,
            "draft_need_rename_folder": False,
            "draft_new_version": "",
            "draft_removable_storage_device": "",
            "draft_root_path": draft_dir.parent.resolve().as_posix(),
            "draft_segment_extra_info": [],
            "draft_timeline_materials_size_": file_size_bytes,
            "draft_type": "",
            "draft_web_article_video_enter_from": "",
            "pippit_avatar_url": "",
            "pippit_extra_info": "",
            "pippit_id": "",
            "pippit_user_name": "",
            "tm_draft_cloud_completed": "",
            "tm_draft_cloud_entry_id": -1,
            "tm_draft_cloud_modified": 0,
            "tm_draft_cloud_parent_entry_id": -1,
            "tm_draft_cloud_space_id": -1,
            "tm_draft_cloud_user_id": -1,
            "tm_draft_create": now_us,
            "tm_draft_modified": now_us,
            "tm_draft_removed": 0,
            "tm_duration": duration_us,
        }

        content_json_str = json.dumps(draft_content, ensure_ascii=False, indent=2)
        meta_json_str = json.dumps(draft_meta_info, ensure_ascii=False, indent=2)

        # Write both draft_content.json (CapCut 9.6.0 Windows) and draft_info.json (CapCut 5/6 & VectCutAPI)
        (draft_dir / "draft_content.json").write_text(content_json_str, encoding="utf-8")
        (draft_dir / "draft_info.json").write_text(content_json_str, encoding="utf-8")
        (draft_dir / "draft_meta_info.json").write_text(meta_json_str, encoding="utf-8")

        # Write auxiliary CapCut desktop files
        (draft_dir / "draft_agency_config.json").write_text(
            json.dumps(
                {
                    "is_auto_agency_enabled": False,
                    "is_auto_agency_popup": False,
                    "is_single_agency_mode": False,
                    "marterials": None,
                    "use_converter": False,
                    "video_resolution": 720,
                }
            ),
            encoding="utf-8",
        )
        (draft_dir / "draft_biz_config.json").write_text("", encoding="utf-8")
        (draft_dir / "draft_virtual_store.json").write_text(
            json.dumps(
                {
                    "draft_materials": [],
                    "draft_virtual_store": [
                        {
                            "type": 0,
                            "value": [
                                {
                                    "creation_time": now_sec,
                                    "display_name": "",
                                    "filter_type": 0,
                                    "id": "",
                                    "import_time": now_sec,
                                    "import_time_us": now_us,
                                    "sort_sub_type": 0,
                                    "sort_type": 0,
                                }
                            ],
                        },
                        {"type": 1, "value": []},
                        {"type": 2, "value": []},
                    ],
                }
            ),
            encoding="utf-8",
        )
        (draft_dir / "attachment_pc_common.json").write_text(
            json.dumps({"ai_packaging_infos": [], "commercial_music_category_ids": []}),
            encoding="utf-8",
        )
        (draft_dir / "timeline_layout.json").write_text(
            json.dumps(
                {
                    "dock_items": [
                        {"dock_index": 0, "Ratio": 1.0, "timeline_ids": [draft_uuid], "type": 0}
                    ],
                    "orientation": 1,
                }
            ),
            encoding="utf-8",
        )
        (draft_dir / "draft_settings").write_text(
            f"[General]\ncloud_last_modify_platform=windows\n"
            f"draft_create_time={now_sec}\ndraft_last_edit_time={now_sec}\n"
            f"real_edit_keys=1\nreal_edit_seconds=1\n",
            encoding="utf-8",
        )

        log.info(
            "Exported CapCut draft for clip '%s' (%.2fs, %dx%d) -> %s",
            spec.clip_id,
            duration_s,
            width,
            height,
            draft_dir,
        )
        return draft_dir


def validate_capcut_draft(draft_dir: Path) -> dict[str, Any]:
    """Smoke-validates a generated CapCut draft directory against CapCut 9.6.0 requirements."""
    required_files = [
        "draft_content.json",
        "draft_info.json",
        "draft_meta_info.json",
        "draft_agency_config.json",
        "draft_biz_config.json",
        "draft_virtual_store.json",
        "draft_settings",
    ]
    missing = [f for f in required_files if not (draft_dir / f).exists()]
    if missing:
        raise ValueError(f"CapCut draft is missing required files: {missing}")

    content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
    meta = json.loads((draft_dir / "draft_meta_info.json").read_text(encoding="utf-8"))

    if content.get("version") != CAPCUT_SCHEMA_VERSION:
        raise ValueError(
            f"Unexpected CapCut schema version: {content.get('version')} != {CAPCUT_SCHEMA_VERSION}"
        )

    duration_us = int(content.get("duration", 0))
    if duration_us <= 0:
        raise ValueError("CapCut draft duration must be positive microseconds")

    canvas = content.get("canvas_config", {})
    if int(canvas.get("width", 0)) <= 0 or int(canvas.get("height", 0)) <= 0:
        raise ValueError(f"Invalid canvas_config in CapCut draft: {canvas}")

    materials = content.get("materials", {})
    videos = materials.get("videos", [])
    texts = materials.get("texts", [])
    speeds = materials.get("speeds", [])

    if not videos:
        raise ValueError("CapCut draft has no video materials")
    if not speeds:
        raise ValueError("CapCut draft has no speed materials")

    video_ids = {v["id"] for v in videos}
    text_ids = {t["id"] for t in texts}

    tracks = content.get("tracks", [])
    video_tracks = [t for t in tracks if t.get("type") == "video"]
    text_tracks = [t for t in tracks if t.get("type") == "text"]

    if not video_tracks:
        raise ValueError("CapCut draft has no video track on timeline")

    video_segments = video_tracks[0].get("segments", [])
    if not video_segments:
        raise ValueError("CapCut video track has no segments")

    for seg in video_segments:
        if seg.get("material_id") not in video_ids:
            raise ValueError(
                f"Video segment references unknown material_id: {seg.get('material_id')}"
            )
        if int(seg.get("target_timerange", {}).get("duration", 0)) <= 0:
            raise ValueError("Video segment target_timerange duration must be > 0")

    for t_track in text_tracks:
        for seg in t_track.get("segments", []):
            if seg.get("material_id") not in text_ids:
                raise ValueError(
                    f"Text segment references unknown material_id: {seg.get('material_id')}"
                )
            if int(seg.get("target_timerange", {}).get("duration", 0)) <= 0:
                raise ValueError("Text segment target_timerange duration must be > 0")

    if int(meta.get("tm_duration", 0)) != duration_us:
        raise ValueError("draft_meta_info.tm_duration does not match draft_content.duration")

    return {
        "valid": True,
        "draft_id": content.get("id"),
        "draft_name": meta.get("draft_name"),
        "duration_us": duration_us,
        "width": canvas.get("width"),
        "height": canvas.get("height"),
        "ratio": canvas.get("ratio"),
        "video_tracks": len(video_tracks),
        "text_tracks": len(text_tracks),
        "video_materials": len(videos),
        "text_materials": len(texts),
    }


def zip_capcut_draft(draft_dir: Path, zip_path: Path | None = None) -> Path:
    """Packages a CapCut draft folder into a downloadable `.zip` archive."""
    if not draft_dir.is_dir():
        raise FileNotFoundError(f"CapCut draft directory not found: {draft_dir}")

    out_zip = zip_path or draft_dir.with_suffix(".zip")
    out_zip.parent.mkdir(parents=True, exist_ok=True)

    folder_name = draft_dir.name
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(draft_dir.rglob("*")):
            if file_path.is_file():
                arcname = f"{folder_name}/{file_path.relative_to(draft_dir).as_posix()}"
                zf.write(file_path, arcname=arcname)

    return out_zip


def get_default_capcut_drafts_dir() -> Path:
    """Returns the standard CapCut Desktop user drafts folder for the current OS."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    return Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"


def install_draft_to_capcut(
    draft_dir: Path,
    capcut_root_dir: Path | None = None,
    draft_name: str | None = None,
) -> Path:
    """Copies a generated draft folder into CapCut's `com.lveditor.draft` directory and updates paths."""
    if not draft_dir.is_dir():
        raise FileNotFoundError(f"Draft folder does not exist: {draft_dir}")

    target_root = (capcut_root_dir or get_default_capcut_drafts_dir()).resolve()
    target_root.mkdir(parents=True, exist_ok=True)

    target_name = draft_name or draft_dir.name
    target_dir = target_root / target_name
    if target_dir.exists():
        shutil.rmtree(target_dir)

    shutil.copytree(draft_dir, target_dir)

    # Update draft_fold_path, draft_root_path, and draft_name in draft_meta_info.json
    meta_file = target_dir / "draft_meta_info.json"
    if meta_file.is_file():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        meta["draft_fold_path"] = target_dir.as_posix()
        meta["draft_root_path"] = str(target_root)
        meta["draft_name"] = target_name
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # If root_meta_info.json exists in target_root, register/update entry safely
        root_meta_file = target_root / "root_meta_info.json"
        if root_meta_file.is_file():
            try:
                root_meta = json.loads(root_meta_file.read_text(encoding="utf-8"))
                store = root_meta.get("all_draft_store")
                if isinstance(store, list):
                    entry = {
                        "cloud_draft_cover": False,
                        "cloud_draft_sync": False,
                        "draft_cloud_last_action_download": False,
                        "draft_cloud_purchase_info": "",
                        "draft_cloud_template_id": "",
                        "draft_cloud_tutorial_info": "",
                        "draft_cloud_videocut_purchase_info": "",
                        "draft_cover": f"{target_dir.as_posix()}\\draft_cover.jpg",
                        "draft_fold_path": target_dir.as_posix(),
                        "draft_id": meta.get("draft_id", _new_capcut_id()),
                        "draft_is_ai_shorts": False,
                        "draft_is_cloud_temp_draft": False,
                        "draft_is_infinite_canvas_draft": False,
                        "draft_is_invisible": False,
                        "draft_is_pippit_draft": False,
                        "draft_is_web_article_video": False,
                        "draft_json_file": f"{target_dir.as_posix()}\\draft_content.json",
                        "draft_name": target_name,
                        "draft_new_version": "",
                        "draft_root_path": target_root.as_posix(),
                        "draft_timeline_materials_size": meta.get(
                            "draft_timeline_materials_size_", 0
                        ),
                        "draft_type": "",
                        "draft_web_article_video_enter_from": "",
                        "pippit_avatar_url": "",
                        "pippit_extra_info": "",
                        "pippit_id": "",
                        "pippit_user_name": "",
                        "streaming_edit_draft_ready": True,
                        "tm_draft_cloud_completed": "",
                        "tm_draft_cloud_entry_id": -1,
                        "tm_draft_cloud_modified": 0,
                        "tm_draft_cloud_parent_entry_id": -1,
                        "tm_draft_cloud_space_id": -1,
                        "tm_draft_cloud_user_id": -1,
                        "tm_draft_create": meta.get("tm_draft_create", int(time.time() * 1e6)),
                        "tm_draft_modified": meta.get("tm_draft_modified", int(time.time() * 1e6)),
                        "tm_draft_removed": 0,
                        "tm_duration": meta.get("tm_duration", 0),
                    }
                    # Remove previous entry with same draft_fold_path or draft_name
                    store = [
                        item
                        for item in store
                        if item.get("draft_name") != target_name
                        and item.get("draft_fold_path") != target_dir.as_posix()
                    ]
                    store.insert(0, entry)
                    root_meta["all_draft_store"] = store
                    root_meta_file.write_text(
                        json.dumps(root_meta, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
            except Exception as exc:
                log.warning("Could not update root_meta_info.json in %s: %s", target_root, exc)

    return target_dir
