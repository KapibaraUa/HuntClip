"""CLI entry point for ClipHunter (plan, Phase 1)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cliphunter.config import get_settings
from cliphunter.export.capcut import (
    CapCutClipSpec,
    CapCutExporter,
    install_draft_to_capcut,
    validate_capcut_draft,
)
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
    run_parser.add_argument(
        "--capcut",
        action="store_true",
        help="Also generate CapCut Desktop drafts for rendered clips",
    )

    # cliphunter estimate <target>
    est_parser = subparsers.add_parser(
        "estimate", help="Estimate tokens and cost for a video without processing"
    )
    est_parser.add_argument("target", help="YouTube URL or path to local video file")

    # cliphunter capcut-export <target>
    cc_parser = subparsers.add_parser(
        "capcut-export",
        help="Export an MP4 clip or job output directory into CapCut Desktop draft(s)",
    )
    cc_parser.add_argument(
        "target",
        type=Path,
        help="Path to a rendered .mp4 clip or job output folder containing manifest.json",
    )
    cc_parser.add_argument(
        "--title",
        type=str,
        default="",
        help="Headline hook text for the draft (when exporting a single .mp4)",
    )
    cc_parser.add_argument(
        "--layout",
        type=str,
        default="center_crop_9x16",
        help="Canvas layout (e.g. center_crop_9x16 or original)",
    )
    cc_parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory where capcut_drafts/ will be created",
    )
    cc_parser.add_argument(
        "--install",
        action="store_true",
        help="Also copy the generated draft into the local CapCut drafts directory",
    )

    # cliphunter serve [--host HOST] [--port PORT]
    serve_parser = subparsers.add_parser("serve", help="Launch FastAPI server and Web Review UI")
    serve_parser.add_argument(
        "--host", type=str, default="127.0.0.1", help="Host interface to bind (default: 127.0.0.1)"
    )
    serve_parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    serve_parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload on code change"
    )

    # cliphunter bot [--token TOKEN]
    bot_parser = subparsers.add_parser("bot", help="Launch Telegram bot for clip generation")
    bot_parser.add_argument(
        "--token", type=str, default=None, help="Telegram bot token (overrides TELEGRAM_TOKEN env)"
    )

    # cliphunter live <url> [--chunk-s CHUNK_S]
    live_parser = subparsers.add_parser(
        "live", help="Record and progressively process a live stream (YouTube/Twitch)"
    )
    live_parser.add_argument("url", help="Live stream URL (YouTube Live, Twitch, etc.)")
    live_parser.add_argument(
        "--chunk-s",
        type=int,
        default=2700,
        help="Segment duration in seconds (default: 2700s / 45m)",
    )
    live_parser.add_argument(
        "--profile",
        type=str,
        default="default",
        help="Profile name for candidate extraction (default: default)",
    )
    live_parser.add_argument(
        "--yes", "-y", action="store_true", help="Automatically render clips without review gate"
    )
    live_parser.add_argument(
        "--max-chunks", type=int, default=None, help="Limit number of chunks to record"
    )

    # cliphunter batch [targets...] [--file FILE]
    batch_parser = subparsers.add_parser(
        "batch", help="Batch process a list of stream URLs or files"
    )
    batch_parser.add_argument(
        "targets",
        nargs="*",
        default=[],
        help="List of URLs or local file paths",
    )
    batch_parser.add_argument(
        "--file",
        "-f",
        type=Path,
        default=None,
        help="Path to text file containing one URL/path per line",
    )
    batch_parser.add_argument(
        "--profile",
        type=Path,
        default=Path("profiles/default.yaml"),
        help="Path to streamer profile YAML",
    )
    batch_parser.add_argument(
        "--budget", type=float, default=None, help="Budget limit in USD per job"
    )
    batch_parser.add_argument(
        "--yes", "-y", action="store_true", help="Automatically confirm all jobs"
    )
    batch_parser.add_argument(
        "--capcut",
        action="store_true",
        help="Also export CapCut Desktop drafts",
    )

    # cliphunter backup [--out-dir DIR] [--keep N] [--restore PATH] [--list]
    backup_parser = subparsers.add_parser(
        "backup", help="Create, list, or restore compressed database backups"
    )
    backup_parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Directory for backup archives (default: data/backups)",
    )
    backup_parser.add_argument(
        "--keep",
        type=int,
        default=7,
        help="Number of recent backup snapshots to retain (default: 7)",
    )
    backup_parser.add_argument(
        "--restore",
        type=Path,
        default=None,
        help="Restore SQLite database from the given backup file",
    )
    backup_parser.add_argument(
        "--list",
        action="store_true",
        help="List existing database backups",
    )

    # cliphunter cleanup [--days N] [--dry-run] [--force-log-rotate]
    cleanup_parser = subparsers.add_parser(
        "cleanup", help="Purge expired sources, proxies, work files, and rotate logs"
    )
    cleanup_parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Retention period in days (default: RETENTION_DAYS from settings)",
    )
    cleanup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report files that would be removed without deleting them",
    )
    cleanup_parser.add_argument(
        "--force-log-rotate",
        action="store_true",
        help="Force rotation of non-empty log files regardless of size",
    )

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
            capcut_export=True if args.capcut else None,
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

    if args.command == "capcut-export":
        target_path: Path = args.target
        if not target_path.exists():
            print(f"Target not found: {target_path}", file=sys.stderr)
            return 1

        if target_path.is_dir():
            out_dir = args.out or target_path
            exporter = CapCutExporter(out_dir=out_dir)
            manifest_file = target_path / "manifest.json"
            drafts: list[Path] = []
            if manifest_file.is_file():
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                for clip_dict in data.get("clips", []):
                    d_path = exporter.export(clip_dict)
                    drafts.append(d_path)
            else:
                for mp4 in sorted(target_path.glob("*.mp4")):
                    d_path = exporter.export(
                        CapCutClipSpec(
                            clip_id=mp4.stem,
                            video_path=mp4,
                            title=args.title or mp4.stem,
                            layout=args.layout,
                        )
                    )
                    drafts.append(d_path)
            for d_path in drafts:
                validate_capcut_draft(d_path)
                print(f"CapCut draft exported: {d_path}")
                if args.install:
                    inst = install_draft_to_capcut(d_path)
                    print(f"Installed to CapCut: {inst}")
            return 0

        out_dir = args.out or target_path.parent
        exporter = CapCutExporter(out_dir=out_dir)
        draft_path = exporter.export(
            CapCutClipSpec(
                clip_id=target_path.stem,
                video_path=target_path,
                title=args.title or target_path.stem,
                layout=args.layout,
            )
        )
        validate_capcut_draft(draft_path)
        print(f"CapCut draft exported: {draft_path}")
        if args.install:
            inst = install_draft_to_capcut(draft_path)
            print(f"Installed to CapCut: {inst}")
        return 0

    if args.command == "serve":
        import uvicorn

        print(f"Starting ClipHunter Web UI on http://{args.host}:{args.port}")
        uvicorn.run(
            "cliphunter.api.app:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return 0

    if args.command == "bot":
        import asyncio

        from cliphunter.bot.telegram import run_bot

        print("Starting ClipHunter Telegram Bot...")
        try:
            asyncio.run(run_bot(token=args.token))
        except (KeyboardInterrupt, SystemExit):
            print("\nBot stopped.")
        return 0

    if args.command == "live":
        import asyncio

        from cliphunter.ingest.live import LiveStreamSession

        print(f"Starting live stream recording session for: {args.url}")
        print(f"Segment duration: {args.chunk_s}s, Profile: {args.profile}")
        session = LiveStreamSession(
            url=args.url,
            profile_id=args.profile,
            chunk_s=args.chunk_s,
            auto_yes=args.yes,
            max_chunks=args.max_chunks,
        )
        try:
            created = asyncio.run(session.run())
            print(f"\nLive session ended. Created {len(created)} segment job(s):")
            for jid in created:
                print(f" - {jid}")
        except (KeyboardInterrupt, SystemExit):
            print("\nLive session stopped.")
        return 0

    if args.command == "batch":
        targets: list[str] = list(args.targets)
        if args.file:
            if not args.file.exists():
                print(f"Batch file not found: {args.file}", file=sys.stderr)
                return 1
            lines = args.file.read_text(encoding="utf-8").splitlines()
            for line in lines:
                clean = line.strip()
                if clean and not clean.startswith("#"):
                    targets.append(clean)

        if not targets:
            print("No targets provided for batch processing.", file=sys.stderr)
            return 1

        print(f"Starting batch queue processing of {len(targets)} video(s)...")
        params = JobParams(
            max_cost_usd=args.budget or settings.max_cost_usd_per_job,
            auto_yes=args.yes,
            profile_path=args.profile,
            capcut_export=True if args.capcut else None,
        )
        runner = PipelineRunner(
            settings=settings,
            confirm_callback=confirm_prompt if not args.yes else None,
        )

        success_count = 0
        for idx, target in enumerate(targets, start=1):
            print(f"\n[{idx}/{len(targets)}] Processing target: {target}")
            try:
                job = runner.run(target=target, params=params, profile_path=args.profile)
                print(
                    f"✓ Job {job.id} completed: {len(job.rendered_clips)} clips, cost ${job.cost_usd:.3f}"
                )
                success_count += 1
            except Exception as exc:
                print(f"✗ Failed to process {target}: {exc}", file=sys.stderr)

        print(f"\nBatch processing finished: {success_count}/{len(targets)} jobs succeeded.")
        return 0 if success_count == len(targets) else 1

    if args.command == "backup":
        from cliphunter.maintenance.backup import (
            backup_database,
            list_backups,
            restore_database,
        )

        if args.list:
            backups = list_backups(out_dir=args.out_dir, settings=settings)
            if not backups:
                print("No database backups found.")
                return 0
            print(f"Found {len(backups)} backup(s):")
            for b in backups:
                print(f" - {b['name']} ({b['size_bytes']} bytes, {b['created_at']})")
            return 0

        if args.restore is not None:
            try:
                restored = restore_database(args.restore, settings=settings)
                print(f"Database restored successfully to: {restored}")
                return 0
            except Exception as exc:
                print(f"Database restore failed: {exc}", file=sys.stderr)
                return 1

        try:
            archive = backup_database(settings=settings, out_dir=args.out_dir, keep_last=args.keep)
            print(f"Database backup created: {archive} ({archive.stat().st_size} bytes)")
            return 0
        except Exception as exc:
            print(f"Database backup failed: {exc}", file=sys.stderr)
            return 1

    if args.command == "cleanup":
        from cliphunter.maintenance.cleanup import cleanup_expired_data

        report = cleanup_expired_data(
            settings=settings,
            retention_days=args.days,
            dry_run=args.dry_run,
            force_log_rotate=args.force_log_rotate,
        )
        mode_str = " (DRY RUN)" if report.dry_run else ""
        print(f"Retention cleanup finished{mode_str} (threshold: {report.retention_days} days):")
        print(f" - Files deleted: {report.deleted_files_count}")
        print(f" - Space freed:   {report.freed_mb:.2f} MB ({report.freed_bytes} bytes)")
        print(f" - Logs rotated:  {report.rotated_logs_count}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
