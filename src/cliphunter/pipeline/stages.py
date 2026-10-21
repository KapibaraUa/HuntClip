"""Pipeline orchestrator: executes stages S0 -> S1 -> S2 -> S3 -> S4 -> S7 with persistence."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from cliphunter.config import Settings
from cliphunter.export.capcut import CapCutExporter
from cliphunter.export.manifest import build_clip_manifest, save_job_manifest
from cliphunter.ingest.probe import probe_source
from cliphunter.ingest.studio import parse_studio_json
from cliphunter.ingest.youtube import download_section
from cliphunter.llm.budget import Budget, estimate_video_cost
from cliphunter.llm.client import GeminiVideoClient
from cliphunter.llm.judge import judge_candidates
from cliphunter.llm.refine import refine_candidates
from cliphunter.llm.scan import scan_chunk
from cliphunter.logging import set_job_id
from cliphunter.models import (
    Candidate,
    ChunkStatus,
    Job,
    JobParams,
    Profile,
    SourceKind,
    StageEnum,
)
from cliphunter.pipeline.chunking import plan_chunks
from cliphunter.pipeline.merge import merge_and_dedupe
from cliphunter.video.cut import cut_candidate
from cliphunter.video.ffmpeg import proxy_cmd, run

log = logging.getLogger(__name__)


def load_profile(path: Path | None) -> Profile:
    if path is None or not path.exists():
        return Profile()
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Profile(**data)


def save_job_state(job: Job, job_dir: Path) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    state_file = job_dir / "state.json"
    state_file.write_text(job.model_dump_json(indent=2), encoding="utf-8")
    return state_file


def load_job_state(job_dir: Path) -> Job | None:
    state_file = job_dir / "state.json"
    if not state_file.exists():
        return None
    try:
        return Job.model_validate_json(state_file.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not load previous state from %s: %s", state_file, exc)
        return None


class PipelineRunner:
    def __init__(
        self,
        settings: Settings,
        client: GeminiVideoClient | None = None,
        confirm_callback: Callable[[float], bool] | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.confirm_callback = confirm_callback

    def run(
        self,
        target: str,
        params: JobParams | None = None,
        profile_path: Path | None = None,
        resume_job_id: str | None = None,
    ) -> Job:
        params = params or JobParams()
        profile = load_profile(profile_path or params.profile_path)

        # 1. S0: Ingest & Probe
        source = probe_source(target)
        job_id = resume_job_id or f"job_{source.id[:10]}"
        job_dir = self.settings.data_dir / "jobs" / job_id
        set_job_id(job_id)

        # Check existing job for resumption
        existing_job = load_job_state(job_dir)
        if existing_job and existing_job.status in ("running", "failed", "paused_budget"):
            log.info("Resuming existing job %s from stage %s", job_id, existing_job.stage)
            job = existing_job
            job.status = "running"
            job.error = None
        else:
            job = Job(
                id=job_id,
                source=source,
                profile=profile,
                params=params,
                stage=StageEnum.S0_INGEST,
            )

        # Client setup
        if self.client is None:
            budget = Budget(limit_usd=params.max_cost_usd, spent_usd=job.cost_usd)
            self.client = GeminiVideoClient(self.settings, budget=budget)
        self.client.job_id = job.id

        try:
            self._execute_pipeline(job, job_dir)
            job.status = "completed"
            job.stage = StageEnum.DONE
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            log.error("Pipeline failed at stage %s: %s", job.stage, exc, exc_info=True)
            save_job_state(job, job_dir)
            raise
        finally:
            job.cost_usd = self.client.budget.spent_usd
            save_job_state(job, job_dir)

        return job

    def _execute_pipeline(self, job: Job, job_dir: Path) -> None:
        source = job.source
        work_dir = job_dir / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        out_dir = Path(job.output_dir or str(self.settings.data_dir / "out" / job.id))
        job.output_dir = str(out_dir)

        # Fast path: Studio JSON provided -> bypass LLM scan/refine/judge (Cost: $0.00 USD)
        if job.params.studio_json:
            log.info(
                "Job %s has studio_json provided: bypassing LLM scan/refine/judge (Cost: $0.00 USD)",
                job.id,
            )
            job.cost_usd = 0.0

            if job.stage == StageEnum.S0_INGEST:
                job.stage = StageEnum.S1_DOWNLOAD_PROXY
                save_job_state(job, job_dir)

            if job.stage == StageEnum.S1_DOWNLOAD_PROXY:
                self._step_s1_download_and_proxy(job, work_dir)
                save_job_state(job, job_dir)

            job.candidates = parse_studio_json(job.params.studio_json)
            job.stage = StageEnum.S7_CUT
            save_job_state(job, job_dir)

            self._step_s7_cut(job, out_dir)
            return

        # S0: Cost estimation & confirmation
        est_cost = estimate_video_cost(
            source.duration_s,
            self.settings.media_res_scan,
            self.settings,
        )
        log.info("Estimated scan cost for %.1fs: $%.4f USD", source.duration_s, est_cost)

        if (
            est_cost > self.settings.confirm_above_usd
            and not job.params.auto_yes
            and self.confirm_callback is not None
            and not self.confirm_callback(est_cost)
        ):
            raise RuntimeError(f"User rejected job execution with estimated cost ${est_cost:.4f}")

        # S1: Download & Proxy
        if job.stage == StageEnum.S0_INGEST:
            job.stage = StageEnum.S1_DOWNLOAD_PROXY
            save_job_state(job, job_dir)

        if job.stage == StageEnum.S1_DOWNLOAD_PROXY:
            self._step_s1_download_and_proxy(job, work_dir)
            job.stage = StageEnum.S2_CHUNK_PLAN
            save_job_state(job, job_dir)

        # S2: Chunk planning
        if job.stage == StageEnum.S2_CHUNK_PLAN:
            if not job.chunks:
                job.chunks = plan_chunks(source.duration_s, self.settings, job_id=job.id)
            job.stage = StageEnum.S3_SCAN
            save_job_state(job, job_dir)

        # S3: Scan (Gemini Pass 1)
        if job.stage == StageEnum.S3_SCAN:
            self._step_s3_scan(job, work_dir)
            job.stage = StageEnum.S4_MERGE
            save_job_state(job, job_dir)

        # S4: Merge & Dedupe
        if job.stage == StageEnum.S4_MERGE:
            job.candidates = merge_and_dedupe(job.candidates, source.duration_s, self.settings)
            job.stage = StageEnum.S5_REFINE
            save_job_state(job, job_dir)

        # S5: Refine (Pass 2) & Snap
        if job.stage == StageEnum.S5_REFINE:
            log.info("Refining %d candidates (Pass 2)...", len(job.candidates))
            assert self.client is not None
            job.candidates = refine_candidates(
                job.candidates, job, self.client, self.settings, work_dir
            )
            job.stage = StageEnum.S6_JUDGE
            save_job_state(job, job_dir)

        # S6: Judge (Pass 3)
        if job.stage == StageEnum.S6_JUDGE:
            log.info("Judging %d refined candidates (Pass 3)...", len(job.candidates))
            assert self.client is not None
            job.candidates = judge_candidates(
                job.candidates, job, self.client, self.settings, work_dir
            )
            job.stage = StageEnum.S7_CUT
            save_job_state(job, job_dir)

        # S7: Cut
        if job.stage == StageEnum.S7_CUT:
            self._step_s7_cut(job, out_dir)

    def _step_s1_download_and_proxy(self, job: Job, work_dir: Path) -> None:
        source = job.source
        proxy_path = work_dir / "proxy_720p.mp4"
        orig_path = work_dir / "source_original.mp4"

        if source.kind == SourceKind.FILE:
            orig_src = Path(source.path or "")
            if not orig_src.exists():
                raise FileNotFoundError(f"Local file not found: {orig_src}")
            job.original_path = str(orig_src)
            if not proxy_path.exists():
                log.info("Generating 720p proxy from local file...")
                run(proxy_cmd(orig_src, proxy_path), timeout_s=self.settings.ffmpeg_timeout_s)
            job.proxy_path = str(proxy_path)
            return

        # YouTube / URL
        if not orig_path.exists():
            log.info("Downloading source video via yt-dlp...")
            assert source.url
            download_section(source.url, 0.0, source.duration_s, orig_path)
        job.original_path = str(orig_path)

        if not proxy_path.exists():
            log.info("Generating 720p proxy from downloaded source...")
            run(proxy_cmd(orig_path, proxy_path), timeout_s=self.settings.ffmpeg_timeout_s)
        job.proxy_path = str(proxy_path)

    def _step_s3_scan(self, job: Job, work_dir: Path) -> None:
        assert self.client is not None
        all_cands: list[Candidate] = list(job.candidates)

        for chunk in job.chunks:
            if chunk.status == ChunkStatus.DONE:
                continue

            log.info(
                "Scanning chunk %d/%d (%.1fs - %.1fs)...",
                chunk.idx + 1,
                len(job.chunks),
                chunk.start_s,
                chunk.end_s,
            )
            cands = scan_chunk(
                chunk=chunk,
                job=job,
                client=self.client,
                settings=self.settings,
                work_dir=work_dir,
            )
            all_cands.extend(cands)
            job.candidates = all_cands
            job.cost_usd = self.client.budget.spent_usd

        job.candidates = all_cands

    def _step_s7_cut(self, job: Job, out_dir: Path) -> None:
        src_path = Path(job.original_path or job.proxy_path or "")
        if not src_path.exists():
            raise FileNotFoundError(f"Source file for cutting not found: {src_path}")

        rendered_manifests: list[dict[str, Any]] = []
        log.info("Rendering %d selected clips to %s...", len(job.candidates), out_dir)

        export_capcut = (
            job.params.capcut_export
            if job.params.capcut_export is not None
            else self.settings.capcut_export
        )
        capcut_exporter = CapCutExporter(out_dir=out_dir) if export_capcut else None

        for i, cand in enumerate(job.candidates, start=1):
            out_clip_path = cut_candidate(
                candidate=cand,
                src=src_path,
                out_dir=out_dir,
                idx=i,
                settings=self.settings,
            )
            manifest = build_clip_manifest(
                candidate=cand,
                job=job,
                output_file=out_clip_path,
                model=self.settings.gemini_model,
                mode="static_scan",
                layout=job.params.layout,
            )
            if capcut_exporter is not None:
                draft_dir = capcut_exporter.export(manifest)
                manifest.capcut_draft_dir = str(draft_dir)

            rendered_manifests.append(manifest.model_dump())

        job.rendered_clips = rendered_manifests
        save_job_manifest(job, out_dir)
