"""Job management and SSE streaming endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from cliphunter.api.sse import broadcaster
from cliphunter.config import Settings
from cliphunter.db.models import JobORM
from cliphunter.db.repo import (
    create_job,
    get_job,
    get_source,
    list_jobs,
    update_job_progress,
    upsert_source,
)
from cliphunter.db.session import get_db
from cliphunter.ingest.probe import probe_source
from cliphunter.llm.budget import estimate_video_cost
from cliphunter.models import Job, JobParams, Profile, Source, SourceKind
from cliphunter.worker.tasks import task_manager

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class CreateJobRequest(BaseModel):
    url: str | None = None
    source_id: str | None = None
    profile_id: str = "default"
    clip_min_s: float = 20.0
    clip_max_s: float = 90.0
    max_cost_usd: float = 5.0
    auto_yes: bool = False


class BatchJobRequest(BaseModel):
    urls: list[str] = []
    profile_id: str = "default"
    clip_min_s: float = 20.0
    clip_max_s: float = 90.0
    max_cost_usd: float = 5.0
    auto_yes: bool = False


class EstimateRequest(BaseModel):
    target: str


def _job_to_dict(j: JobORM) -> dict[str, Any]:
    has_source = "source" in j.__dict__ and j.source is not None
    has_candidates = "candidates" in j.__dict__ and j.candidates is not None
    has_clips = "clips" in j.__dict__ and j.clips is not None
    return {
        "id": j.id,
        "source_id": j.source_id,
        "source_title": j.source.title if has_source else "Unknown",
        "source_duration_s": j.source.duration_s if has_source else 0.0,
        "source_url": j.source.url if has_source else None,
        "source_kind": j.source.kind if has_source else "unknown",
        "profile_id": j.profile_id,
        "stage": j.stage,
        "status": j.status,
        "cost_usd": j.cost_usd,
        "budget_usd": j.budget_usd,
        "original_path": j.original_path,
        "proxy_path": j.proxy_path,
        "output_dir": j.output_dir,
        "error": j.error,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "updated_at": j.updated_at.isoformat() if j.updated_at else None,
        "candidates_count": len(j.candidates) if has_candidates else 0,
        "clips_count": len(j.clips) if has_clips else 0,
    }


@router.post("")
async def create_new_job(
    req: CreateJobRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Creates a new stream highlight job and launches pipeline processing."""
    if not req.url and not req.source_id:
        raise HTTPException(status_code=400, detail="Either url or source_id must be provided")

    source_meta: Source | None = None

    if req.source_id:
        s_orm = await get_source(session, req.source_id)
        if not s_orm:
            raise HTTPException(status_code=404, detail=f"Source {req.source_id} not found")
        source_meta = Source(
            id=s_orm.id,
            kind=SourceKind(s_orm.kind),
            url=s_orm.url,
            path=s_orm.path,
            duration_s=s_orm.duration_s,
            title=s_orm.title,
            channel=s_orm.channel,
            sha256=s_orm.sha256,
        )
    elif req.url:
        try:
            source_meta = probe_source(req.url)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to probe URL: {exc}") from exc
        await upsert_source(session, source_meta)

    assert source_meta is not None
    job_id = f"job_{uuid.uuid4().hex[:10]}"

    job = Job(
        id=job_id,
        source=source_meta,
        profile=Profile(name=req.profile_id),
        params=JobParams(
            clip_min_s=req.clip_min_s,
            clip_max_s=req.clip_max_s,
            max_cost_usd=req.max_cost_usd,
            auto_yes=req.auto_yes,
        ),
    )

    await create_job(session, job)
    await session.commit()

    loaded_job = await get_job(session, job_id)
    assert loaded_job is not None

    # Launch background pipeline execution
    task_manager.start_job(job_id)

    return _job_to_dict(loaded_job)


@router.post("/batch")
async def create_batch_jobs(
    req: BatchJobRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Creates multiple stream highlight jobs in batch and starts them in background."""
    if not req.urls:
        raise HTTPException(status_code=400, detail="urls list cannot be empty")

    batch_id = f"batch_{uuid.uuid4().hex[:8]}"
    created_jobs: list[dict[str, Any]] = []

    for url in req.urls:
        url_clean = url.strip()
        if not url_clean:
            continue
        try:
            source_meta = probe_source(url_clean)
            await upsert_source(session, source_meta)

            job_id = f"job_{uuid.uuid4().hex[:10]}"
            job = Job(
                id=job_id,
                source=source_meta,
                profile=Profile(name=req.profile_id),
                params=JobParams(
                    clip_min_s=req.clip_min_s,
                    clip_max_s=req.clip_max_s,
                    max_cost_usd=req.max_cost_usd,
                    auto_yes=req.auto_yes,
                ),
            )
            await create_job(session, job)
            await session.commit()

            loaded_job = await get_job(session, job_id)
            assert loaded_job is not None
            task_manager.start_job(job_id)
            created_jobs.append(_job_to_dict(loaded_job))
        except Exception as exc:
            created_jobs.append({"url": url_clean, "error": str(exc), "status": "failed"})

    return {
        "batch_id": batch_id,
        "total": len(req.urls),
        "created_count": len([j for j in created_jobs if "id" in j]),
        "jobs": created_jobs,
    }


@router.get("")
async def list_all_jobs(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Returns a list of recent jobs."""
    jobs = await list_jobs(session, limit=limit, offset=offset)
    return [_job_to_dict(j) for j in jobs]


@router.get("/{job_id}")
async def get_job_by_id(
    job_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Returns full details of a job."""
    job = await get_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_dict(job)


@router.get("/{job_id}/events")
async def stream_job_events(job_id: str) -> EventSourceResponse:
    """Streams real-time SSE progress events for a job."""
    return EventSourceResponse(broadcaster.subscribe(job_id))


@router.post("/{job_id}/cancel")
async def cancel_job_execution(
    job_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Cancels a running background pipeline task."""
    cancelled = task_manager.cancel_job(job_id)
    await update_job_progress(session, job_id, status="cancelled")
    await session.commit()
    await broadcaster.broadcast(job_id, "cancelled", {"status": "cancelled"})
    return {"job_id": job_id, "cancelled": cancelled}


@router.post("/{job_id}/retry-stage")
async def retry_job_stage(
    job_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Resumes or retries a job."""
    job = await get_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    await update_job_progress(session, job_id, status="running", error=None)
    await session.commit()
    task_manager.start_job(job_id)
    return {"job_id": job_id, "resumed": True}


@router.post("/estimate")
async def estimate_stream_cost(
    req: EstimateRequest,
) -> dict[str, Any]:
    """Estimates duration, tokens, and cost for a target video."""
    settings = Settings()
    try:
        source = probe_source(req.target)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to probe target: {exc}") from exc

    est_cost = estimate_video_cost(source.duration_s, settings.media_res_scan, settings)
    return {
        "title": source.title,
        "duration_s": source.duration_s,
        "duration_min": round(source.duration_s / 60.0, 1),
        "channel": source.channel,
        "estimated_cost_usd": round(est_cost, 4),
        "confirm_required": est_cost > settings.confirm_above_usd,
    }
