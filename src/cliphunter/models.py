"""Domain models for pipeline state, entities, and manifests (plan, sections 6 & 10)."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from cliphunter.llm.schemas import Category, CutRange


class SourceKind(enum.StrEnum):
    YOUTUBE_URL = "youtube_url"
    FILE = "file"
    OTHER_URL = "other_url"


class Source(BaseModel):
    id: str
    kind: SourceKind
    url: str | None = None
    path: str | None = None
    duration_s: float
    title: str = "Unknown"
    channel: str = "Unknown"
    sha256: str | None = None


class StageEnum(enum.StrEnum):
    S0_INGEST = "s0_ingest"
    S1_DOWNLOAD_PROXY = "s1_download_proxy"
    S2_CHUNK_PLAN = "s2_chunk_plan"
    S3_SCAN = "s3_scan"
    S4_MERGE = "s4_merge"
    S5_REFINE = "s5_refine"
    S6_JUDGE = "s6_judge"
    S7_CUT = "s7_cut"
    DONE = "done"
    FAILED = "failed"


class ChunkStatus(enum.StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    PARTIAL = "partial"  # e.g., if safety blocked or partially failed
    FAILED = "failed"


class Chunk(BaseModel):
    id: str
    idx: int
    start_s: float
    end_s: float
    status: ChunkStatus = ChunkStatus.PENDING
    file_uri: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


class CandidateStatus(enum.StrEnum):
    RAW = "raw"
    MERGED = "merged"
    REFINED = "refined"
    DROPPED = "dropped"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    RENDERED = "rendered"


class Candidate(BaseModel):
    id: str
    chunk_id: str | None = None
    start_s: float  # global timeline seconds
    end_s: float  # global timeline seconds
    category: Category
    working_title: str
    score: int = Field(ge=1, le=10)
    status: CandidateStatus = CandidateStatus.RAW
    source_pass: Literal["scan", "refine", "judge", "manual"] = "scan"
    prompt_version: int = 1
    visual_hook: str = ""
    hook_text: str | None = None
    what_happens: str = ""
    visual_evidence: str = ""
    audio_evidence: str = ""
    why_viral: str = ""
    ending_text: str | None = None
    cuttable_inside: list[CutRange] = Field(default_factory=list)
    title_options: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


class Profile(BaseModel):
    name: str = "default"
    language: str = "ru"
    game: str = ""
    facecam_rect: dict[str, float] | None = None
    gameplay_rect: dict[str, float] | None = None
    clip_min_s: float = 20.0
    clip_max_s: float = 90.0
    prompt_addon: str = ""
    template: str | None = None
    watermark: str | None = None


class JobParams(BaseModel):
    clip_min_s: float = 20.0
    clip_max_s: float = 90.0
    max_cost_usd: float = 5.0
    auto_yes: bool = False
    profile_path: Path | None = None
    layout: str = "original"
    capcut_export: bool | None = None


class Job(BaseModel):
    id: str
    source: Source
    profile: Profile = Field(default_factory=Profile)
    params: JobParams = Field(default_factory=JobParams)
    stage: StageEnum = StageEnum.S0_INGEST
    status: Literal["running", "paused_budget", "awaiting_review", "completed", "failed"] = (
        "running"
    )
    chunks: list[Chunk] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    cost_usd: float = 0.0
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    error: str | None = None
    original_path: str | None = None
    proxy_path: str | None = None
    output_dir: str | None = None
    rendered_clips: list[dict[str, Any]] = Field(default_factory=list)


class ClipManifest(BaseModel):
    id: str
    source_url_or_path: str
    start_s: float
    end_s: float
    duration_s: float
    category: Category
    score: int
    working_title: str
    title_options: list[str]
    visual_evidence: str
    audio_evidence: str
    why_viral: str
    model: str
    mode: str
    prompt_version: int
    layout: str = "original"
    template: str = "default"
    capcut_draft_dir: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    output_file: str
