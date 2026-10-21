"""SQLAlchemy ORM models for ClipHunter (plan, Section 6)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

# JSONType that works with both PostgreSQL (JSONB/JSON) and SQLite (JSON)
JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class SourceORM(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # youtube_url, file, other_url
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    duration_s: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="Unknown")
    channel: Mapped[str] = mapped_column(String(256), nullable=False, default="Unknown")
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )

    jobs: Mapped[list[JobORM]] = relationship(
        "JobORM", back_populates="source", cascade="all, delete-orphan"
    )


class JobORM(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(64), ForeignKey("sources.id"), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    params: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="s0_ingest")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )  # pending, running, awaiting_review, rendering, completed, failed, cancelled
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    budget_usd: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    original_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    proxy_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    output_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )

    source: Mapped[SourceORM] = relationship("SourceORM", back_populates="jobs")
    chunks: Mapped[list[ChunkORM]] = relationship(
        "ChunkORM", back_populates="job", cascade="all, delete-orphan"
    )
    candidates: Mapped[list[CandidateORM]] = relationship(
        "CandidateORM", back_populates="job", cascade="all, delete-orphan"
    )
    clips: Mapped[list[ClipORM]] = relationship(
        "ClipORM", back_populates="job", cascade="all, delete-orphan"
    )
    api_calls: Mapped[list[ApiCallORM]] = relationship(
        "ApiCallORM", back_populates="job", cascade="all, delete-orphan"
    )


class ChunkORM(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.id"), nullable=False)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    file_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    job: Mapped[JobORM] = relationship("JobORM", back_populates="chunks")


class CandidateORM(Base):
    __tablename__ = "candidates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.id"), nullable=False)
    chunk_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_s: Mapped[float] = mapped_column(Float, nullable=False)
    end_s: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    working_title: Mapped[str] = mapped_column(String(512), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="raw"
    )  # raw, merged, refined, dropped, accepted, rejected, rendered
    data: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    source_pass: Mapped[str] = mapped_column(
        String(32), nullable=False, default="scan"
    )  # scan, refine, judge, manual
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )

    job: Mapped[JobORM] = relationship("JobORM", back_populates="candidates")
    clips: Mapped[list[ClipORM]] = relationship("ClipORM", back_populates="candidate")


class ClipORM(Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("jobs.id"), nullable=False)
    candidate_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("candidates.id"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    layout: Mapped[str] = mapped_column(String(64), nullable=False, default="original")
    template: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    duration_s: Mapped[float] = mapped_column(Float, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )

    job: Mapped[JobORM] = relationship("JobORM", back_populates="clips")
    candidate: Mapped[CandidateORM] = relationship("CandidateORM", back_populates="clips")


class ApiCallORM(Base):
    __tablename__ = "api_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("jobs.id"), nullable=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(64), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )

    job: Mapped[JobORM | None] = relationship("JobORM", back_populates="api_calls")


class ProfileORM(Base):
    __tablename__ = "profiles"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="ru")
    game: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    facecam_rect: Mapped[dict[str, float] | None] = mapped_column(JSON_TYPE, nullable=True)
    gameplay_rect: Mapped[dict[str, float] | None] = mapped_column(JSON_TYPE, nullable=True)
    clip_min_s: Mapped[float] = mapped_column(Float, nullable=False, default=20.0)
    clip_max_s: Mapped[float] = mapped_column(Float, nullable=False, default=90.0)
    prompt_addon: Mapped[str] = mapped_column(Text, nullable=False, default="")
    template: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    watermark: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )
