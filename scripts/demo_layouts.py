"""Demo script to render 3 visual sample clips for Phase 4 owner inspection (plan, Section 14).

Usage:
  uv run python scripts/demo_layouts.py [--source path/to/stream.mp4]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cliphunter.config import Settings
from cliphunter.models import Candidate, Profile
from cliphunter.video.ffmpeg import run
from cliphunter.video.render import render_candidate, verify_rendered_clip_dimensions


def ensure_sample_source(target_path: Path) -> Path:
    """Generates a test video if no source is provided."""
    if target_path.is_file():
        return target_path

    target_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Generating synthetic 8s stream test source: {target_path}...")
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=8:size=1920x1080:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=8",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(target_path),
    ]
    run(cmd, timeout_s=30)
    return target_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate 3 visual layout demo clips for review")
    parser.add_argument("--source", type=Path, default=Path("data/demo_source.mp4"))
    parser.add_argument("--out", type=Path, default=Path("data/demo_clips"))
    args = parser.parse_args()

    source_path = ensure_sample_source(args.source)
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    profile = Profile(
        name="evelone2004",
        facecam_rect={"x": 0.72, "y": 0.05, "w": 0.25, "h": 0.3},
        gameplay_rect={"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
    )

    demo_specs = [
        (
            1,
            "original",
            "16:9 Исходный формат с нормализацией звука loudnorm и хук-титром",
            "Невероятный клатч 1 в 4",
        ),
        (
            2,
            "blur_fit_9x16",
            "9:16 Размытый фон (Blur Fit) для TikTok / Reels / Shorts",
            "Реакция на донат 100 000 руб",
        ),
        (
            3,
            "facecam_top_game_bottom_9x16",
            "9:16 Вертикальный сплит (Фейскам сверху, геймплей снизу)",
            "Стрим: Evelone192 эпичный момент",
        ),
    ]

    print("\n--- Рендеринг 3 демонстрационных клипов для визуальной проверки ---")
    results = []
    for idx, layout, desc, title in demo_specs:
        cand = Candidate(
            id=f"demo_{idx}",
            start_s=0.5,
            end_s=4.5,  # 4.0 seconds duration
            category="game_moment",
            working_title=title,
            score=9,
        )

        out_file = render_candidate(
            candidate=cand,
            src=source_path,
            out_dir=out_dir,
            idx=idx,
            layout=layout,
            profile=profile,
            settings=settings,
            title=title,
        )

        stats = verify_rendered_clip_dimensions(out_file, expected_dur_s=4.0, layout=layout)
        results.append((idx, layout, desc, out_file, stats))
        print(
            f"[{idx}/3] {layout}: {out_file.name} ({stats['width']}x{stats['height']}, {stats['actual_duration_s']:.1f}s)"
        )

    print("\nУспешно создано 3 клипа для визуальной проверки:")
    for idx, _layout, desc, out_file, _stats in results:
        print(f"  {idx}. {desc}")
        print(f"     Файл: {out_file.resolve()}")
    print(
        "\nОткрой эти файлы в любом плеере или браузере для визуальной оценки качества монтажа.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
