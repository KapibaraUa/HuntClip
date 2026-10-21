"""Unit and smoke tests for CapCut draft exporter (Phase 5, Section 10)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from cliphunter.export.capcut import (
    CAPCUT_SCHEMA_VERSION,
    CAPCUT_VERIFIED_VERSION,
    CapCutClipSpec,
    CapCutExporter,
    Exporter,
    install_draft_to_capcut,
    parse_ass_subtitles,
    sec_to_us,
    validate_capcut_draft,
    zip_capcut_draft,
)
from cliphunter.models import ClipManifest
from cliphunter.video.subtitles import SubtitleWord, generate_ass_file

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "capcut_9_6_0_reference.json"


def test_exporter_protocol_conformance(tmp_path: Path) -> None:
    exporter = CapCutExporter(out_dir=tmp_path)
    assert isinstance(exporter, Exporter)


def test_sec_to_us_conversion() -> None:
    assert sec_to_us(0.0) == 0
    assert sec_to_us(1.5) == 1_500_000
    assert sec_to_us(28.733333) == 28_733_333
    assert sec_to_us(-5.0) == 0


def test_smoke_capcut_reference_fixture(tmp_path: Path) -> None:
    """Smoke test validating generated draft against CapCut 9.6.0.4097 fixture contract."""
    ref = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert ref["capcut_desktop_version"] == CAPCUT_VERIFIED_VERSION
    assert ref["schema_version"] == CAPCUT_SCHEMA_VERSION

    dummy_mp4 = tmp_path / "01_clutch_moment.mp4"
    dummy_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    exporter = CapCutExporter(out_dir=tmp_path)
    draft_dir = exporter.export(
        CapCutClipSpec(
            clip_id="clip_01_smoke",
            video_path=dummy_mp4,
            title="Невероятный клатч 1 в 4",
            duration_s=24.5,
            layout="facecam_top_game_bottom_9x16",
        )
    )

    assert draft_dir == tmp_path / "capcut_drafts" / "clip_01_smoke"

    for fname in ref["required_draft_files"]:
        assert (draft_dir / fname).exists(), f"Missing draft file: {fname}"

    content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
    info = json.loads((draft_dir / "draft_info.json").read_text(encoding="utf-8"))
    meta = json.loads((draft_dir / "draft_meta_info.json").read_text(encoding="utf-8"))

    assert content == info
    for k in ref["required_content_keys"]:
        assert k in content, f"Missing content key: {k}"

    for k in ref["required_materials_keys"]:
        assert k in content["materials"], f"Missing materials key: {k}"

    video_mat = content["materials"]["videos"][0]
    for k in ref["required_video_material_keys"]:
        assert k in video_mat, f"Missing video material key: {k}"

    video_seg = content["tracks"][0]["segments"][0]
    for k in ref["required_video_segment_keys"]:
        assert k in video_seg, f"Missing video segment key: {k}"

    text_mat = content["materials"]["texts"][0]
    for k in ref["required_text_material_keys"]:
        assert k in text_mat, f"Missing text material key: {k}"

    for k in ref["required_meta_keys"]:
        assert k in meta, f"Missing draft_meta_info key: {k}"

    # Verify timeline placement & title hook content
    assert content["duration"] == 24_500_000
    assert content["canvas_config"]["width"] == 1080
    assert content["canvas_config"]["height"] == 1920
    assert content["canvas_config"]["ratio"] == "9:16"
    assert video_seg["target_timerange"] == {"start": 0, "duration": 24_500_000}

    parsed_text_content = json.loads(text_mat["content"])
    assert parsed_text_content["text"] == "Невероятный клатч 1 в 4"
    assert parsed_text_content["styles"][0]["range"] == [0, len("Невероятный клатч 1 в 4")]

    validation = validate_capcut_draft(draft_dir)
    assert validation["valid"] is True
    assert validation["video_tracks"] == 1
    assert validation["text_tracks"] == 1


def test_export_from_clip_manifest_and_horizontal_layout(tmp_path: Path) -> None:
    clip_file = tmp_path / "horizontal_clip.mp4"
    clip_file.write_bytes(b"fake_mp4_bytes")

    manifest = ClipManifest(
        id="cand_42",
        source_url_or_path="https://youtube.com/watch?v=test",
        start_s=120.0,
        end_s=155.0,
        duration_s=35.0,
        category="game_moment",
        score=9,
        working_title="Финальный раунд на Mirage",
        title_options=["Финальный раунд", "Клатч года"],
        visual_evidence="1v3 на пленте A",
        audio_evidence="Крик стримера",
        why_viral="Высокое напряжение",
        model="gemini-3.8-flash",
        mode="static_scan",
        prompt_version=1,
        layout="original",
        template="default",
        output_file=str(clip_file),
    )

    exporter = CapCutExporter(out_dir=tmp_path, copy_media=True)
    draft_dir = exporter.export(manifest)

    content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
    assert content["canvas_config"]["width"] == 1920
    assert content["canvas_config"]["height"] == 1080
    assert content["canvas_config"]["ratio"] == "16:9"

    copied_media = draft_dir / "Resources" / "video" / "horizontal_clip.mp4"
    assert copied_media.is_file()
    assert content["materials"]["videos"][0]["path"] == copied_media.resolve().as_posix()


def test_export_with_ass_subtitles(tmp_path: Path) -> None:
    clip_file = tmp_path / "subtitled.mp4"
    clip_file.write_bytes(b"mp4")

    words = [
        SubtitleWord("Это", 0.2, 0.5),
        SubtitleWord("просто", 0.5, 0.9),
        SubtitleWord("невероятный", 0.9, 1.5),
        SubtitleWord("момент!", 1.5, 2.1),
        SubtitleWord("Смотрите", 2.3, 2.8),
        SubtitleWord("до", 2.8, 3.0),
        SubtitleWord("конца!", 3.0, 3.6),
    ]
    ass_path = generate_ass_file(words, tmp_path / "subs.ass", words_per_line=4)
    parsed_lines = parse_ass_subtitles(ass_path)
    assert len(parsed_lines) == 2
    assert parsed_lines[0].word == "Это просто невероятный момент!"
    assert parsed_lines[1].word == "Смотрите до конца!"

    exporter = CapCutExporter(out_dir=tmp_path)
    draft_dir = exporter.export(
        CapCutClipSpec(
            clip_id="clip_with_subs",
            video_path=clip_file,
            title="Заголовок клипа",
            duration_s=10.0,
            ass_path=ass_path,
        )
    )

    validation = validate_capcut_draft(draft_dir)
    assert validation["valid"] is True
    assert validation["text_tracks"] == 2  # 1 title_hook + 1 subtitles
    assert validation["text_materials"] == 3  # 1 title + 2 subtitle lines


def test_zip_and_install_draft_to_capcut(tmp_path: Path) -> None:
    clip_file = tmp_path / "demo.mp4"
    clip_file.write_bytes(b"mp4data")

    exporter = CapCutExporter(out_dir=tmp_path)
    draft_dir = exporter.export(
        {
            "clip_id": "clip_zip_test",
            "video_path": str(clip_file),
            "title": "Тест архива и установки",
            "duration_s": 12.0,
            "layout": "blur_fit_9x16",
        }
    )

    # Test zip packaging
    zip_file = zip_capcut_draft(draft_dir)
    assert zip_file.is_file()
    with zipfile.ZipFile(zip_file, "r") as zf:
        names = zf.namelist()
        assert "clip_zip_test/draft_content.json" in names
        assert "clip_zip_test/draft_meta_info.json" in names

    # Test installing into simulated CapCut com.lveditor.draft directory with root_meta_info.json
    capcut_root = tmp_path / "com.lveditor.draft"
    capcut_root.mkdir(parents=True)
    root_meta_path = capcut_root / "root_meta_info.json"
    root_meta_path.write_text(
        json.dumps({"all_draft_store": [], "draft_ids": 1, "root_path": capcut_root.as_posix()}),
        encoding="utf-8",
    )

    installed_dir = install_draft_to_capcut(
        draft_dir, capcut_root_dir=capcut_root, draft_name="cliphunter_installed"
    )
    assert installed_dir == capcut_root / "cliphunter_installed"
    assert (installed_dir / "draft_content.json").is_file()

    installed_meta = json.loads(
        (installed_dir / "draft_meta_info.json").read_text(encoding="utf-8")
    )
    assert installed_meta["draft_name"] == "cliphunter_installed"
    assert installed_meta["draft_fold_path"] == installed_dir.as_posix()

    updated_root_meta = json.loads(root_meta_path.read_text(encoding="utf-8"))
    assert len(updated_root_meta["all_draft_store"]) == 1
    assert updated_root_meta["all_draft_store"][0]["draft_name"] == "cliphunter_installed"
