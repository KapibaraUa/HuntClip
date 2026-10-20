"""Manifest export for rendered clips and complete jobs (plan, Section 10)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cliphunter.models import Candidate, ClipManifest, Job


def build_clip_manifest(
    candidate: Candidate,
    job: Job,
    output_file: Path,
    model: str,
    mode: str,
) -> ClipManifest:
    source_ref = job.source.url or job.source.path or "unknown"
    return ClipManifest(
        id=candidate.id,
        source_url_or_path=source_ref,
        start_s=candidate.start_s,
        end_s=candidate.end_s,
        duration_s=candidate.duration_s,
        category=candidate.category,
        score=candidate.score,
        working_title=candidate.working_title,
        title_options=candidate.title_options,
        visual_evidence=candidate.visual_evidence,
        audio_evidence=candidate.audio_evidence,
        why_viral=candidate.why_viral,
        model=model,
        mode=mode,
        prompt_version=candidate.prompt_version,
        output_file=str(output_file),
    )


def save_job_manifest(job: Job, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = out_dir / "manifest.json"

    data: dict[str, Any] = {
        "job_id": job.id,
        "source": job.source.model_dump(),
        "created_at": job.created_at,
        "total_cost_usd": job.cost_usd,
        "total_chunks": len(job.chunks),
        "total_candidates": len(job.candidates),
        "total_rendered_clips": len(job.rendered_clips),
        "clips": job.rendered_clips,
    }

    manifest_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_file
