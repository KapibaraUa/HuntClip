"""Candidate review, boundary patching, manual moment addition, and rendering trigger."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from cliphunter.api.sse import broadcaster
from cliphunter.db.models import CandidateORM
from cliphunter.db.repo import (
    add_manual_candidate,
    get_candidate,
    get_candidates,
    get_job,
    update_candidate,
)
from cliphunter.db.session import get_db
from cliphunter.worker.tasks import task_manager

router = APIRouter(tags=["candidates"])


class PatchCandidateRequest(BaseModel):
    start_s: float | None = None
    end_s: float | None = None
    status: str | None = None  # accepted, rejected, dropped
    working_title: str | None = None
    score: int | None = Field(None, ge=1, le=10)


class AddCandidateRequest(BaseModel):
    start_s: float
    end_s: float
    working_title: str
    category: str = "game_moment"
    score: int = Field(8, ge=1, le=10)
    why_viral: str = "Manually selected highlight"


class RenderJobRequest(BaseModel):
    selected_candidate_ids: list[str] | None = None
    layout: str = "original"
    template: str = "default"


def _cand_to_dict(c: CandidateORM) -> dict[str, Any]:
    return {
        "id": c.id,
        "job_id": c.job_id,
        "start_s": c.start_s,
        "end_s": c.end_s,
        "duration_s": round(c.end_s - c.start_s, 2),
        "category": c.category,
        "working_title": c.working_title,
        "score": c.score,
        "status": c.status,
        "source_pass": c.source_pass,
        "why_viral": c.data.get("why_viral", ""),
        "what_happens": c.data.get("what_happens", ""),
        "visual_evidence": c.data.get("visual_evidence", ""),
        "audio_evidence": c.data.get("audio_evidence", ""),
        "title_options": c.data.get("title_options", []),
        "cuttable_inside": c.data.get("cuttable_inside", []),
    }


@router.get("/api/jobs/{job_id}/candidates")
async def list_job_candidates(
    job_id: str,
    session: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Lists all candidates found for a job."""
    cands = await get_candidates(session, job_id)
    return [_cand_to_dict(c) for c in cands]


@router.patch("/api/candidates/{candidate_id}")
async def patch_candidate_boundaries_and_status(
    candidate_id: str,
    req: PatchCandidateRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Updates candidate timecodes, status (accepted/rejected), title, or score."""
    cand = await get_candidate(session, candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if req.start_s is not None and req.end_s is not None and req.end_s <= req.start_s:
        raise HTTPException(status_code=400, detail="end_s must be greater than start_s")

    updated = await update_candidate(
        session,
        candidate_id,
        start_s=req.start_s,
        end_s=req.end_s,
        status=req.status,
        working_title=req.working_title,
        score=req.score,
    )
    await session.commit()
    assert updated is not None

    # Broadcast candidate update event to SSE subscribers
    await broadcaster.broadcast(
        updated.job_id,
        "candidate_patched",
        {"candidate": _cand_to_dict(updated)},
    )

    return _cand_to_dict(updated)


@router.post("/api/jobs/{job_id}/candidates")
async def add_candidate_manually(
    job_id: str,
    req: AddCandidateRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Allows user to manually add a highlight candidate moment."""
    job = await get_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if req.end_s <= req.start_s:
        raise HTTPException(status_code=400, detail="end_s must be greater than start_s")

    cand_orm = await add_manual_candidate(
        session,
        job_id=job_id,
        start_s=req.start_s,
        end_s=req.end_s,
        working_title=req.working_title,
        category=req.category,
        score=req.score,
        why_viral=req.why_viral,
    )
    await session.commit()

    await broadcaster.broadcast(
        job_id,
        "candidate_added",
        {"candidate": _cand_to_dict(cand_orm)},
    )

    return _cand_to_dict(cand_orm)


@router.post("/api/jobs/{job_id}/render")
async def render_job_candidates(
    job_id: str,
    req: RenderJobRequest | None = None,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Renders accepted candidates into final MP4 clips and manifests."""
    job = await get_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    selected_ids = req.selected_candidate_ids if req else None
    layout = req.layout if req else "original"
    template = req.template if req else "default"

    # Trigger render in background or await
    clips = await task_manager.render_job(
        job_id,
        selected_candidate_ids=selected_ids,
        layout=layout,
        template=template,
    )

    return {
        "job_id": job_id,
        "rendered_count": len(clips),
        "clips": [
            {
                "id": c.id,
                "path": c.path,
                "duration_s": c.duration_s,
                "candidate_id": c.candidate_id,
                "manifest": c.manifest,
            }
            for c in clips
        ],
    }
