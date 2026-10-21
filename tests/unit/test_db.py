import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cliphunter.db.models import Base
from cliphunter.db.repo import (
    add_manual_candidate,
    create_job,
    get_candidates,
    list_jobs,
    sync_job_candidates,
    update_candidate,
    update_job_progress,
    upsert_source,
)
from cliphunter.db.session import Database
from cliphunter.models import Candidate, Job, JobParams, Source, SourceKind


@pytest.fixture
async def db() -> Database:
    test_db = Database("sqlite+aiosqlite:///:memory:")
    async with test_db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return test_db


@pytest.fixture
async def session(db: Database) -> AsyncSession:
    async with db.session_factory() as s:
        yield s


@pytest.mark.anyio
async def test_db_source_and_job_lifecycle(session: AsyncSession) -> None:
    source = Source(
        id="src_test_123",
        kind=SourceKind.YOUTUBE_URL,
        url="https://youtube.com/watch?v=123",
        duration_s=3600.0,
        title="Epic Stream",
        channel="TopGamer",
    )
    s_orm = await upsert_source(session, source)
    assert s_orm.id == "src_test_123"
    assert s_orm.title == "Epic Stream"

    job = Job(
        id="job_abc_456",
        source=source,
        params=JobParams(clip_min_s=15.0, clip_max_s=45.0),
    )
    j_orm = await create_job(session, job)
    assert j_orm.id == "job_abc_456"
    assert j_orm.stage == "s0_ingest"

    # List jobs
    jobs = await list_jobs(session)
    assert len(jobs) == 1
    assert jobs[0].id == "job_abc_456"

    # Update progress
    updated = await update_job_progress(
        session, "job_abc_456", stage="s3_scan", status="running", cost_usd=0.05
    )
    assert updated is not None
    assert updated.stage == "s3_scan"
    assert updated.cost_usd == 0.05


@pytest.mark.anyio
async def test_db_candidates_and_manual_add(session: AsyncSession) -> None:
    source = Source(
        id="src_test_cand",
        kind=SourceKind.FILE,
        duration_s=1200.0,
    )
    job = Job(
        id="job_cand_1",
        source=source,
        params=JobParams(),
    )
    await create_job(session, job)

    cand1 = Candidate(
        id="c1",
        chunk_idx=0,
        start_s=10.0,
        end_s=25.0,
        category="funny",
        working_title="Laughing fit",
        score=9,
        why_viral="Pure comedy",
    )
    await sync_job_candidates(session, "job_cand_1", [cand1])

    cands = await get_candidates(session, "job_cand_1")
    assert len(cands) == 1
    assert cands[0].working_title == "Laughing fit"
    assert cands[0].score == 9

    # Patch candidate boundaries
    patched = await update_candidate(session, "c1", start_s=9.5, end_s=26.0, status="accepted")
    assert patched is not None
    assert patched.start_s == 9.5
    assert patched.end_s == 26.0
    assert patched.status == "accepted"

    # Add manual candidate
    manual = await add_manual_candidate(
        session,
        job_id="job_cand_1",
        start_s=100.0,
        end_s=125.0,
        working_title="Manual Highlight",
        category="game_moment",
        score=10,
    )
    assert manual.id.startswith("manual_")
    assert manual.source_pass == "manual"
    assert manual.status == "accepted"

    all_cands = await get_candidates(session, "job_cand_1")
    assert len(all_cands) == 2
