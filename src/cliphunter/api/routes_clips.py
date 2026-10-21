"""Endpoints for clip download, clip retrieval, and job token/cost usage breakdown."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from cliphunter.db.models import ClipORM
from cliphunter.db.repo import get_clip, get_clips_for_job, get_job, get_job_api_calls
from cliphunter.db.session import get_db
from cliphunter.export.capcut import (
    CapCutClipSpec,
    CapCutExporter,
    validate_capcut_draft,
    zip_capcut_draft,
)

router = APIRouter(tags=["clips"])


def _clip_to_dict(c: ClipORM) -> dict[str, Any]:
    manifest = c.manifest or {}
    return {
        "id": c.id,
        "job_id": c.job_id,
        "candidate_id": c.candidate_id,
        "path": c.path,
        "layout": c.layout,
        "template": c.template,
        "duration_s": c.duration_s,
        "manifest": manifest,
        "capcut_draft_dir": manifest.get("capcut_draft_dir"),
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "download_url": f"/api/clips/{c.id}/download",
        "capcut_export_url": f"/api/clips/{c.id}/capcut",
        "capcut_download_url": f"/api/clips/{c.id}/capcut/download",
        "stream_url": f"/api/media/clips/{c.id}",
    }


@router.get("/api/jobs/{job_id}/clips")
async def list_job_clips(
    job_id: str,
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Lists all rendered clips for a given job."""
    clips = await get_clips_for_job(session, job_id)
    return [_clip_to_dict(c) for c in clips]


@router.get("/api/clips/{clip_id}")
async def get_clip_details(
    clip_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Retrieves metadata and download link for a rendered clip."""
    clip = await get_clip(session, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    return _clip_to_dict(clip)


@router.get("/api/clips/{clip_id}/download")
async def download_clip_file(
    clip_id: str,
    session: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Downloads the rendered clip MP4 file as an attachment."""
    clip = await get_clip(session, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    file_path = Path(clip.path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Clip file not found on disk")

    filename = file_path.name
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="video/mp4",
    )


async def _ensure_capcut_draft(clip: ClipORM, session: AsyncSession) -> Path:
    """Exports or returns existing CapCut draft directory for a clip."""
    file_path = Path(clip.path)
    out_dir = file_path.parent if file_path.parent.name else Path(f"data/jobs/{clip.job_id}/out")
    manifest = dict(clip.manifest or {})
    title = str(manifest.get("title") or manifest.get("working_title") or clip.id)

    exporter = CapCutExporter(out_dir=out_dir)
    draft_dir = exporter.export(
        CapCutClipSpec(
            clip_id=clip.id,
            video_path=file_path,
            title=title,
            duration_s=clip.duration_s,
            layout=clip.layout,
            template=clip.template,
        )
    )
    manifest["capcut_draft_dir"] = str(draft_dir)
    clip.manifest = manifest
    await session.commit()
    return draft_dir


@router.post("/api/clips/{clip_id}/capcut")
async def export_clip_capcut_draft(
    clip_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Generates a CapCut Desktop draft folder for a rendered clip."""
    clip = await get_clip(session, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    draft_dir = await _ensure_capcut_draft(clip, session)
    validation = validate_capcut_draft(draft_dir)
    return {
        "clip_id": clip.id,
        "draft_dir": str(draft_dir),
        "download_url": f"/api/clips/{clip.id}/capcut/download",
        "validation": validation,
    }


@router.get("/api/clips/{clip_id}/capcut/download")
async def download_clip_capcut_zip(
    clip_id: str,
    session: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Downloads the CapCut draft folder packaged as a `.zip` archive."""
    clip = await get_clip(session, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    draft_dir = await _ensure_capcut_draft(clip, session)
    zip_path = zip_capcut_draft(draft_dir)
    return FileResponse(
        path=str(zip_path),
        filename=f"{clip.id}_capcut_draft.zip",
        media_type="application/zip",
    )


@router.get("/api/jobs/{job_id}/usage")
async def get_job_usage_stats(
    job_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Returns token usage and cost accounting breakdown for a job."""
    job = await get_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    api_calls = await get_job_api_calls(session, job_id)

    total_tokens_in = sum(call.tokens_in for call in api_calls)
    total_tokens_out = sum(call.tokens_out for call in api_calls)
    total_cost_usd = sum(call.cost_usd for call in api_calls)
    cache_hits = sum(1 for call in api_calls if call.cache_hit)
    cache_hit_rate = (cache_hits / len(api_calls)) if api_calls else 0.0

    stages_breakdown: dict[str, dict[str, Any]] = {}
    for call in api_calls:
        stage = call.stage or "unknown"
        if stage not in stages_breakdown:
            stages_breakdown[stage] = {
                "calls": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "latency_ms_total": 0,
            }
        b = stages_breakdown[stage]
        b["calls"] += 1
        b["tokens_in"] += call.tokens_in
        b["tokens_out"] += call.tokens_out
        b["cost_usd"] = round(b["cost_usd"] + call.cost_usd, 6)
        b["latency_ms_total"] += call.latency_ms

    calls_log = [
        {
            "id": call.id,
            "stage": call.stage,
            "model": call.model,
            "mode": call.mode,
            "tokens_in": call.tokens_in,
            "tokens_out": call.tokens_out,
            "cost_usd": call.cost_usd,
            "latency_ms": call.latency_ms,
            "cache_hit": call.cache_hit,
            "created_at": call.created_at.isoformat() if call.created_at else None,
        }
        for call in api_calls
    ]

    cands = job.candidates or []
    clips_list = job.clips or []
    funnel = {
        "total": len(cands),
        "raw": sum(1 for c in cands if c.status == "raw"),
        "merged": sum(1 for c in cands if c.status == "merged"),
        "refined": sum(1 for c in cands if c.status == "refined"),
        "accepted": sum(1 for c in cands if c.status in ("accepted", "rendered")),
        "dropped": sum(1 for c in cands if c.status in ("dropped", "rejected")),
        "rendered": len(clips_list),
    }

    return {
        "job_id": job_id,
        "total_calls": len(api_calls),
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_tokens": total_tokens_in + total_tokens_out,
        "total_cost_usd": round(total_cost_usd, 4),
        "job_recorded_cost_usd": job.cost_usd,
        "budget_usd": job.budget_usd,
        "cache_hits": cache_hits,
        "cache_hit_rate": round(cache_hit_rate, 2),
        "funnel": funnel,
        "stages": stages_breakdown,
        "calls": calls_log,
    }
