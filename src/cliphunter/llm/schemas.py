"""Response schemas (plan, Appendix B). Clip duration is not in the schema: code computes it."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Category = Literal[
    "emotion", "game_moment", "story", "funny", "opinion", "fact_advice", "conflict", "quote"
]


class CutRange(BaseModel):
    start: str  # MM:SS
    end: str


class ScanClip(BaseModel):
    start: str = Field(description="MM:SS или HH:MM:SS от начала переданного фрагмента")
    end: str
    confidence_timestamps: Literal["high", "medium", "low"]
    category: Category
    working_title: str
    hook_text: str | None = None
    visual_hook: str
    what_happens: str
    visual_evidence: str
    audio_evidence: str
    why_viral: str
    ending_text: str | None = None
    cuttable_inside: list[CutRange] = []
    title_options: list[str]
    score: int = Field(ge=1, le=10)
    risks: list[str] = []


class ScanResponse(BaseModel):
    notes: str | None = None
    clips: list[ScanClip]


class RefineResponse(BaseModel):
    verdict: Literal["keep", "drop"]
    start: str | None = None
    end: str | None = None
    cut_inside: list[CutRange] = []
    score: int = Field(ge=1, le=10)
    reason: str


class JudgeItem(BaseModel):
    clip_index: int = Field(ge=1)
    verdict: Literal["keep", "drop"]
    score: int = Field(ge=1, le=10)
    hook: int = Field(ge=1, le=10)
    emotion: int = Field(ge=1, le=10)
    completeness: int = Field(ge=1, le=10)
    standalone: int = Field(ge=1, le=10)
    reason: str
    title_options: list[str]


class JudgeResponse(BaseModel):
    items: list[JudgeItem]


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema passed to the API as response_format.schema (see docs/CALIBRATION.md)."""
    return model.model_json_schema()
