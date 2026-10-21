"""Repository CRUD operations for ClipHunter database."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cliphunter.db.models import (
    ApiCallORM,
    CandidateORM,
    ClipORM,
    JobORM,
    ProfileORM,
    SourceORM,
)
from cliphunter.models import Candidate, Job, Profile, Source


async def upsert_source(session: AsyncSession, source: Source) -> SourceORM:
    stmt = select(SourceORM).where(SourceORM.id == source.id)
    res = await session.execute(stmt)
    obj = res.scalar_one_or_none()
    if obj is None:
        obj = SourceORM(
            id=source.id,
            kind=source.kind.value if hasattr(source.kind, "value") else str(source.kind),
            url=source.url,
            path=source.path,
            duration_s=source.duration_s,
            title=source.title,
            channel=source.channel,
            sha256=source.sha256,
            status="ready",
        )
        session.add(obj)
    else:
        obj.title = source.title
        obj.channel = source.channel
        obj.duration_s = source.duration_s
        obj.path = source.path
        obj.url = source.url
    await session.flush()
    return obj


async def get_source(session: AsyncSession, source_id: str) -> SourceORM | None:
    stmt = select(SourceORM).where(SourceORM.id == source_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def create_job(session: AsyncSession, job: Job) -> JobORM:
    # Ensure source exists first
    await upsert_source(session, job.source)

    profile_name = job.profile.name if getattr(job, "profile", None) else "default"
    budget = getattr(job.params, "max_cost_usd", 5.0)

    job_orm = JobORM(
        id=job.id,
        source_id=job.source.id,
        profile_id=profile_name,
        params=job.params.model_dump(),
        stage=job.stage.value if hasattr(job.stage, "value") else str(job.stage),
        status=job.status,
        cost_usd=job.cost_usd,
        budget_usd=budget,
        original_path=job.original_path,
        proxy_path=job.proxy_path,
        output_dir=job.output_dir,
        error=job.error,
    )
    session.add(job_orm)
    await session.flush()
    return job_orm


async def get_job(session: AsyncSession, job_id: str) -> JobORM | None:
    stmt = (
        select(JobORM)
        .where(JobORM.id == job_id)
        .options(
            selectinload(JobORM.source),
            selectinload(JobORM.candidates),
            selectinload(JobORM.clips),
            selectinload(JobORM.chunks),
        )
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def list_jobs(session: AsyncSession, limit: int = 50, offset: int = 0) -> list[JobORM]:
    stmt = (
        select(JobORM)
        .options(selectinload(JobORM.source), selectinload(JobORM.clips))
        .order_by(desc(JobORM.created_at))
        .limit(limit)
        .offset(offset)
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def update_job_progress(
    session: AsyncSession,
    job_id: str,
    *,
    stage: str | None = None,
    status: str | None = None,
    cost_usd: float | None = None,
    error: str | None = None,
    original_path: str | None = None,
    proxy_path: str | None = None,
    output_dir: str | None = None,
) -> JobORM | None:
    job = await get_job(session, job_id)
    if job is None:
        return None

    if stage is not None:
        job.stage = stage
    if status is not None:
        job.status = status
    if cost_usd is not None:
        job.cost_usd = cost_usd
    if error is not None:
        job.error = error
    if original_path is not None:
        job.original_path = original_path
    if proxy_path is not None:
        job.proxy_path = proxy_path
    if output_dir is not None:
        job.output_dir = output_dir

    job.updated_at = dt.datetime.now(dt.UTC)
    await session.flush()
    return job


async def sync_job_candidates(
    session: AsyncSession, job_id: str, candidates: list[Candidate]
) -> None:
    """Inserts or updates candidates from pipeline into the database."""
    for cand in candidates:
        stmt = select(CandidateORM).where(CandidateORM.id == cand.id)
        res = await session.execute(stmt)
        c_orm = res.scalar_one_or_none()

        extra_data = {
            "why_viral": cand.why_viral,
            "what_happens": cand.what_happens,
            "visual_evidence": cand.visual_evidence,
            "audio_evidence": cand.audio_evidence,
            "visual_hook": cand.visual_hook,
            "hook_text": cand.hook_text,
            "ending_text": cand.ending_text,
            "title_options": cand.title_options,
            "risks": cand.risks,
            "cuttable_inside": [c.model_dump() for c in cand.cuttable_inside],
        }

        cat = str(cand.category)
        stat = str(cand.status.value) if hasattr(cand.status, "value") else str(cand.status)

        if c_orm is None:
            c_orm = CandidateORM(
                id=cand.id,
                job_id=job_id,
                chunk_id=cand.chunk_id,
                start_s=cand.start_s,
                end_s=cand.end_s,
                category=cat,
                working_title=cand.working_title,
                score=cand.score,
                status=stat,
                data=extra_data,
                source_pass=cand.source_pass,
                prompt_version=cand.prompt_version,
            )
            session.add(c_orm)
        else:
            c_orm.start_s = cand.start_s
            c_orm.end_s = cand.end_s
            c_orm.score = cand.score
            c_orm.status = stat
            c_orm.source_pass = cand.source_pass
            c_orm.working_title = cand.working_title
            c_orm.data = extra_data

    await session.flush()


async def get_candidates(session: AsyncSession, job_id: str) -> list[CandidateORM]:
    stmt = (
        select(CandidateORM)
        .where(CandidateORM.job_id == job_id)
        .order_by(desc(CandidateORM.score), CandidateORM.start_s)
    )
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def get_candidate(session: AsyncSession, candidate_id: str) -> CandidateORM | None:
    stmt = select(CandidateORM).where(CandidateORM.id == candidate_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def update_candidate(
    session: AsyncSession,
    candidate_id: str,
    *,
    start_s: float | None = None,
    end_s: float | None = None,
    status: str | None = None,
    working_title: str | None = None,
    score: int | None = None,
) -> CandidateORM | None:
    cand = await get_candidate(session, candidate_id)
    if cand is None:
        return None

    if start_s is not None:
        cand.start_s = round(start_s, 2)
    if end_s is not None:
        cand.end_s = round(end_s, 2)
    if status is not None:
        cand.status = status
    if working_title is not None:
        cand.working_title = working_title
    if score is not None:
        cand.score = score

    await session.flush()
    return cand


async def add_manual_candidate(
    session: AsyncSession,
    *,
    job_id: str,
    start_s: float,
    end_s: float,
    working_title: str,
    category: str = "game_moment",
    score: int = 8,
    why_viral: str = "Manually selected highlight",
) -> CandidateORM:
    cand_id = f"manual_{uuid.uuid4().hex[:8]}"
    cand_orm = CandidateORM(
        id=cand_id,
        job_id=job_id,
        start_s=round(start_s, 2),
        end_s=round(end_s, 2),
        category=category,
        working_title=working_title,
        score=score,
        status="accepted",  # manual candidate is accepted by default
        source_pass="manual",
        data={
            "why_viral": why_viral,
            "what_happens": working_title,
            "visual_evidence": "User selected via review UI",
            "audio_evidence": "User selected via review UI",
            "title_options": [working_title],
        },
    )
    session.add(cand_orm)
    await session.flush()
    return cand_orm


async def save_clip(session: AsyncSession, clip_orm: ClipORM) -> ClipORM:
    session.add(clip_orm)
    await session.flush()
    return clip_orm


async def get_clips_for_job(session: AsyncSession, job_id: str) -> list[ClipORM]:
    stmt = select(ClipORM).where(ClipORM.job_id == job_id).order_by(ClipORM.created_at)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def get_clip(session: AsyncSession, clip_id: str) -> ClipORM | None:
    stmt = select(ClipORM).where(ClipORM.id == clip_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def record_api_call(
    session: AsyncSession,
    *,
    job_id: str | None,
    stage: str,
    model: str,
    mode: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    latency_ms: int,
    cache_hit: bool,
    error: str | None = None,
) -> ApiCallORM:
    call_orm = ApiCallORM(
        job_id=job_id,
        stage=stage,
        model=model,
        mode=mode,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        error=error,
    )
    session.add(call_orm)
    await session.flush()
    return call_orm


async def get_job_api_calls(session: AsyncSession, job_id: str) -> list[ApiCallORM]:
    stmt = select(ApiCallORM).where(ApiCallORM.job_id == job_id).order_by(ApiCallORM.created_at)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def list_profiles(session: AsyncSession) -> list[ProfileORM]:
    stmt = select(ProfileORM).order_by(ProfileORM.name)
    res = await session.execute(stmt)
    return list(res.scalars().all())


async def get_profile(session: AsyncSession, name: str) -> ProfileORM | None:
    stmt = select(ProfileORM).where(ProfileORM.name == name)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def upsert_profile(session: AsyncSession, profile: Profile) -> ProfileORM:
    p = await get_profile(session, profile.name)
    if p is None:
        p = ProfileORM(
            name=profile.name,
            language=profile.language,
            game=profile.game,
            facecam_rect=profile.facecam_rect,
            gameplay_rect=profile.gameplay_rect,
            clip_min_s=profile.clip_min_s,
            clip_max_s=profile.clip_max_s,
            prompt_addon=profile.prompt_addon,
        )
        session.add(p)
    else:
        p.language = profile.language
        p.game = profile.game
        p.facecam_rect = profile.facecam_rect
        p.gameplay_rect = profile.gameplay_rect
        p.clip_min_s = profile.clip_min_s
        p.clip_max_s = profile.clip_max_s
        p.prompt_addon = profile.prompt_addon
    await session.flush()
    return p
