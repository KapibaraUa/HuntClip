"""Async background pipeline execution worker and task manager."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

from cliphunter.api.sse import broadcaster
from cliphunter.config import Settings
from cliphunter.db.models import ClipORM
from cliphunter.db.repo import (
    get_candidates,
    get_job,
    save_clip,
    sync_job_candidates,
    update_job_progress,
    upsert_source,
)
from cliphunter.db.session import Database, get_db_manager
from cliphunter.ingest.probe import probe_source
from cliphunter.ingest.youtube import download_section
from cliphunter.llm.budget import Budget
from cliphunter.llm.client import GeminiVideoClient
from cliphunter.llm.judge import judge_candidates
from cliphunter.llm.refine import refine_candidates
from cliphunter.llm.scan import scan_chunk
from cliphunter.logging import set_job_id
from cliphunter.models import (
    Candidate,
    CandidateStatus,
    ChunkStatus,
    Job,
    JobParams,
    Source,
    SourceKind,
    StageEnum,
)
from cliphunter.pipeline.chunking import plan_chunks
from cliphunter.pipeline.merge import merge_and_dedupe
from cliphunter.pipeline.stages import load_profile
from cliphunter.video.cut import cut_candidate
from cliphunter.video.ffmpeg import proxy_cmd, run

log = logging.getLogger(__name__)


class JobTaskManager:
    """Manages active background execution tasks for jobs."""

    def __init__(self, db: Database | None = None, settings: Settings | None = None) -> None:
        self.db = db or get_db_manager()
        self.settings = settings or Settings()
        self._active_tasks: dict[str, asyncio.Task[None]] = {}

    def is_running(self, job_id: str) -> bool:
        task = self._active_tasks.get(job_id)
        return task is not None and not task.done()

    def start_job(
        self,
        job_id: str,
        client: GeminiVideoClient | None = None,
    ) -> None:
        """Launches job execution in background task."""
        if self.is_running(job_id):
            log.warning("Job %s is already running", job_id)
            return

        task = asyncio.create_task(self._run_job_wrapper(job_id, client))
        self._active_tasks[job_id] = task

    def cancel_job(self, job_id: str) -> bool:
        """Cancels a running background task."""
        task = self._active_tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def _run_job_wrapper(self, job_id: str, client: GeminiVideoClient | None = None) -> None:
        try:
            await self._run_pipeline(job_id, client)
        except asyncio.CancelledError:
            log.info("Job %s was cancelled by user", job_id)
            async with self.db.session_factory() as session:
                await update_job_progress(session, job_id, status="cancelled")
                await session.commit()
            await broadcaster.broadcast(job_id, "cancelled", {"status": "cancelled"})
        except Exception as exc:
            log.exception("Job %s failed: %s", job_id, exc)
            async with self.db.session_factory() as session:
                await update_job_progress(session, job_id, status="failed", error=str(exc))
                await session.commit()
            await broadcaster.broadcast(job_id, "failed", {"error": str(exc)})
        finally:
            self._active_tasks.pop(job_id, None)

    async def _run_pipeline(self, job_id: str, client: GeminiVideoClient | None = None) -> None:
        set_job_id(job_id)
        log.info("Starting background pipeline for job %s", job_id)

        # 1. Load job from DB
        async with self.db.session_factory() as session:
            job_orm = await get_job(session, job_id)
            if not job_orm:
                raise ValueError(f"Job {job_id} not found in database")

            source_orm = job_orm.source
            source = Source(
                id=source_orm.id,
                kind=SourceKind(source_orm.kind),
                url=source_orm.url,
                path=source_orm.path,
                duration_s=source_orm.duration_s,
                title=source_orm.title,
                channel=source_orm.channel,
                sha256=source_orm.sha256,
            )
            params = JobParams(**(job_orm.params or {}))
            profile = load_profile(params.profile_path)
            profile.name = job_orm.profile_id

            job = Job(
                id=job_orm.id,
                source=source,
                profile=profile,
                params=params,
                stage=StageEnum(job_orm.stage),
                status=cast(Any, job_orm.status),
                cost_usd=job_orm.cost_usd,
                original_path=job_orm.original_path,
                proxy_path=job_orm.proxy_path,
                output_dir=job_orm.output_dir,
            )

        job_dir = Path(self.settings.data_dir) / "jobs" / job.id
        work_dir = job_dir / "work"
        out_dir = Path(job.output_dir) if job.output_dir else (job_dir / "out")
        work_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        job.output_dir = str(out_dir)

        # Setup Gemini client
        if client is None:
            budget = Budget(job.params.max_cost_usd)
            client = GeminiVideoClient(self.settings, budget=budget)

        # S0: Ingest
        await self._broadcast_stage(job.id, "s0_ingest", "Probing media source metadata...")
        if source.duration_s <= 0.0:
            target = source.url or source.path or ""
            source = probe_source(target)
            job.source = source
            async with self.db.session_factory() as session:
                await upsert_source(session, source)
                await session.commit()

        # S1: Download & Proxy
        await self._broadcast_stage(
            job.id, "s1_download_proxy", "Downloading and generating 720p proxy..."
        )
        proxy_path = work_dir / "proxy_720p.mp4"
        orig_path = work_dir / "source_original.mp4"

        if source.kind == SourceKind.FILE:
            orig_src = Path(source.path or "")
            if not orig_src.exists():
                raise FileNotFoundError(f"Local file not found: {orig_src}")
            job.original_path = str(orig_src)
            if not proxy_path.exists():
                run(proxy_cmd(orig_src, proxy_path), timeout_s=self.settings.ffmpeg_timeout_s)
            job.proxy_path = str(proxy_path)
        else:
            if not orig_path.exists():
                assert source.url
                download_section(source.url, 0.0, source.duration_s, orig_path)
            job.original_path = str(orig_path)
            if not proxy_path.exists():
                run(proxy_cmd(orig_path, proxy_path), timeout_s=self.settings.ffmpeg_timeout_s)
            job.proxy_path = str(proxy_path)

        async with self.db.session_factory() as session:
            await update_job_progress(
                session,
                job.id,
                stage="s1_download_proxy",
                original_path=job.original_path,
                proxy_path=job.proxy_path,
            )
            await session.commit()

        # S2: Chunk planning
        await self._broadcast_stage(job.id, "s2_chunk_plan", "Planning video chunks...")
        if not job.chunks:
            job.chunks = plan_chunks(source.duration_s, self.settings, job_id=job.id)

        # S3: Scan (Pass 1)
        await self._broadcast_stage(
            job.id, "s3_scan", f"Scanning {len(job.chunks)} chunks with Gemini..."
        )
        all_cands: list[Candidate] = list(job.candidates)

        for i, chunk in enumerate(job.chunks):
            if chunk.status == ChunkStatus.DONE:
                continue

            await broadcaster.broadcast(
                job.id,
                "progress",
                {
                    "stage": "s3_scan",
                    "chunk_current": i + 1,
                    "chunk_total": len(job.chunks),
                    "progress_pct": int(((i + 1) / len(job.chunks)) * 100),
                    "spent_usd": client.budget.spent_usd,
                },
            )

            cands = scan_chunk(
                chunk=chunk,
                job=job,
                client=client,
                settings=self.settings,
                work_dir=work_dir,
            )
            all_cands.extend(cands)
            job.candidates = all_cands
            job.cost_usd = client.budget.spent_usd

            # Sync progress to DB
            async with self.db.session_factory() as session:
                await sync_job_candidates(session, job.id, cands)
                await update_job_progress(session, job.id, cost_usd=job.cost_usd)
                await session.commit()

        job.candidates = all_cands

        # S4: Merge & Dedupe
        await self._broadcast_stage(
            job.id, "s4_merge", f"Merging and deduping {len(job.candidates)} candidates..."
        )
        job.candidates = merge_and_dedupe(job.candidates, source.duration_s, self.settings)
        async with self.db.session_factory() as session:
            await sync_job_candidates(session, job.id, job.candidates)
            await update_job_progress(session, job.id, stage="s4_merge")
            await session.commit()

        # S5: Refine (Pass 2) & Snap
        await self._broadcast_stage(
            job.id, "s5_refine", f"Refining {len(job.candidates)} candidates (high resolution)..."
        )
        job.candidates = refine_candidates(job.candidates, job, client, self.settings, work_dir)
        job.cost_usd = client.budget.spent_usd
        async with self.db.session_factory() as session:
            await sync_job_candidates(session, job.id, job.candidates)
            await update_job_progress(session, job.id, stage="s5_refine", cost_usd=job.cost_usd)
            await session.commit()

        # S6: Judge (Pass 3)
        await self._broadcast_stage(
            job.id, "s6_judge", f"Judging {len(job.candidates)} candidates in batches..."
        )
        job.candidates = judge_candidates(job.candidates, job, client, self.settings, work_dir)
        job.cost_usd = client.budget.spent_usd
        async with self.db.session_factory() as session:
            await sync_job_candidates(session, job.id, job.candidates)
            await update_job_progress(session, job.id, stage="s6_judge", cost_usd=job.cost_usd)
            await session.commit()

        # Review Gate
        if not self.settings.auto_render and not job.params.auto_yes:
            log.info("Job %s stopped for human review at awaiting_review", job.id)
            async with self.db.session_factory() as session:
                await update_job_progress(session, job.id, stage="review", status="awaiting_review")
                await session.commit()

            await broadcaster.broadcast(
                job.id,
                "awaiting_review",
                {
                    "status": "awaiting_review",
                    "candidates_count": len(job.candidates),
                    "spent_usd": job.cost_usd,
                },
            )
            return

        # Auto Render if enabled
        await self.render_job(job.id, selected_candidate_ids=None)

    async def render_job(
        self,
        job_id: str,
        selected_candidate_ids: list[str] | None = None,
    ) -> list[ClipORM]:
        """Renders accepted candidates into mp4 clips and saves them to DB."""
        await self._broadcast_stage(job_id, "s7_cut", "Rendering final clips with FFmpeg...")

        async with self.db.session_factory() as session:
            job_orm = await get_job(session, job_id)
            if not job_orm:
                raise ValueError(f"Job {job_id} not found")

            src_path = Path(job_orm.original_path or job_orm.proxy_path or "")
            if not src_path.exists():
                raise FileNotFoundError(f"Source file not found: {src_path}")

            out_dir = Path(job_orm.output_dir or f"data/jobs/{job_id}/out")
            out_dir.mkdir(parents=True, exist_ok=True)

            cands_orm = await get_candidates(session, job_id)
            to_render = [
                c
                for c in cands_orm
                if c.status == "accepted"
                and (selected_candidate_ids is None or c.id in selected_candidate_ids)
            ]

            rendered_clips: list[ClipORM] = []

            for idx, c_orm in enumerate(to_render, start=1):
                cand = Candidate(
                    id=c_orm.id,
                    chunk_id=c_orm.chunk_id,
                    start_s=c_orm.start_s,
                    end_s=c_orm.end_s,
                    category=cast(Any, c_orm.category),
                    working_title=c_orm.working_title,
                    score=c_orm.score,
                    status=CandidateStatus(c_orm.status),
                    why_viral=c_orm.data.get("why_viral", ""),
                    what_happens=c_orm.data.get("what_happens", ""),
                    visual_evidence=c_orm.data.get("visual_evidence", ""),
                    audio_evidence=c_orm.data.get("audio_evidence", ""),
                    title_options=c_orm.data.get("title_options", []),
                )

                out_clip_path = cut_candidate(
                    candidate=cand,
                    src=src_path,
                    out_dir=out_dir,
                    idx=idx,
                    settings=self.settings,
                )

                clip_orm = ClipORM(
                    id=f"clip_{c_orm.id}",
                    job_id=job_id,
                    candidate_id=c_orm.id,
                    path=str(out_clip_path),
                    layout="original",
                    template="default",
                    duration_s=cand.duration_s,
                    manifest={
                        "candidate_id": cand.id,
                        "title": cand.working_title,
                        "start_s": cand.start_s,
                        "end_s": cand.end_s,
                        "duration_s": cand.duration_s,
                        "score": cand.score,
                        "title_options": cand.title_options,
                    },
                )
                await save_clip(session, clip_orm)
                rendered_clips.append(clip_orm)
                c_orm.status = "rendered"

            await update_job_progress(session, job_id, stage="done", status="completed")
            await session.commit()

        await broadcaster.broadcast(
            job_id,
            "completed",
            {
                "status": "completed",
                "clips_rendered": len(rendered_clips),
                "clips": [
                    {
                        "id": c.id,
                        "path": c.path,
                        "duration_s": c.duration_s,
                        "candidate_id": c.candidate_id,
                    }
                    for c in rendered_clips
                ],
            },
        )
        return rendered_clips

    async def _broadcast_stage(self, job_id: str, stage: str, message: str) -> None:
        async with self.db.session_factory() as session:
            await update_job_progress(session, job_id, stage=stage, status="running")
            await session.commit()

        await broadcaster.broadcast(
            job_id,
            "stage_start",
            {"stage": stage, "message": message},
        )


# Global task manager instance
task_manager = JobTaskManager()
