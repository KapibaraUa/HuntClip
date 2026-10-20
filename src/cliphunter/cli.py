"""CLI entry point for ClipHunter (plan, Phase 1)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cliphunter.config import get_settings
from cliphunter.ingest.probe import probe_source
from cliphunter.llm.budget import estimate_video_cost
from cliphunter.logging import setup_logging
from cliphunter.models import JobParams
from cliphunter.pipeline.stages import PipelineRunner


def confirm_prompt(cost_usd: float) -> bool:
    try:
        reply = input(f"Estimated cost is ${cost_usd:.4f} USD. Proceed? [y/N]: ").strip().lower()
        return reply in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cliphunter",
        description="ClipHunter: AI stream clipper powered by Gemini video understanding.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # cliphunter run <target>
    run_parser = subparsers.add_parser(
        "run", help="Run the clipping pipeline on a video URL or local file"
    )
    run_parser.add_argument("target", help="YouTube URL or path to local video file")
    run_parser.add_argument(
        "--profile",
        type=Path,
        default=Path("profiles/default.yaml"),
        help="Path to streamer profile YAML",
    )
    run_parser.add_argument(
        "--budget", type=float, default=None, help="Budget limit in USD for this job"
    )
    run_parser.add_argument(
        "--yes", "-y", action="store_true", help="Automatically confirm estimated costs"
    )
    run_parser.add_argument(
        "--min-len", type=float, default=None, help="Min clip length in seconds"
    )
    run_parser.add_argument(
        "--max-len", type=float, default=None, help="Max clip length in seconds"
    )
    run_parser.add_argument(
        "--out", type=Path, default=None, help="Custom output directory for clips"
    )

    # cliphunter estimate <target>
    est_parser = subparsers.add_parser(
        "estimate", help="Estimate tokens and cost for a video without processing"
    )
    est_parser.add_argument("target", help="YouTube URL or path to local video file")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level, settings.secrets())

    if args.command == "estimate":
        source = probe_source(args.target)
        cost = estimate_video_cost(source.duration_s, settings.media_res_scan, settings)
        print(f"Source: {source.title} ({source.channel})")
        print(f"Duration: {source.duration_s:.1f}s ({source.duration_s / 60:.1f} min)")
        print(f"Estimated Scan Cost (Pass 1 @ low): ~${cost:.4f} USD")
        return 0

    if args.command == "run":
        params = JobParams(
            clip_min_s=args.min_len or settings.clip_min_s,
            clip_max_s=args.max_len or settings.clip_max_s,
            max_cost_usd=args.budget or settings.max_cost_usd_per_job,
            auto_yes=args.yes,
            profile_path=args.profile,
        )

        runner = PipelineRunner(
            settings=settings,
            confirm_callback=confirm_prompt if not args.yes else None,
        )

        try:
            job = runner.run(target=args.target, params=params, profile_path=args.profile)
            print("\nPipeline finished successfully!")
            print(f"Job ID: {job.id}")
            print(f"Found & rendered clips: {len(job.rendered_clips)}")
            print(f"Total spent: ${job.cost_usd:.4f} USD")
            print(f"Clips directory: {job.output_dir}")
            return 0
        except Exception as exc:
            print(f"\nPipeline error: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
