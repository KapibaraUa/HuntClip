"""Application settings. Every number, threshold, model and price lives here (see plan, section 12)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

MediaRes = Literal["low", "medium", "high", "unspecified"]
ThinkingLevel = Literal["minimal", "low", "medium", "high"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # Gemini
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.8-flash"
    agentic_mode: bool = False
    thinking_level: ThinkingLevel = "medium"
    media_res_scan: MediaRes = "low"
    media_res_refine: MediaRes = "high"
    refine_fps: float = Field(1.0, gt=0)

    # Chunking
    chunk_s: int = Field(2700, gt=0)
    overlap_s: int = Field(120, ge=0)
    short_video_max_s: int = Field(2700, gt=0)
    pad_before_s: float = 30.0
    pad_after_s: float = 30.0
    snap_s: float = 1.0

    # Clips and candidate limits
    clip_min_s: float = 20.0
    clip_max_s: float = 90.0
    clip_tolerance_s: float = 5.0
    max_candidates_per_hour: int = 20
    max_per_30min: int = 6
    judge_batch: int = Field(10, ge=1, le=10)
    llm_concurrency: int = Field(2, ge=1)

    # Budget and prices (USD per 1M tokens)
    max_cost_usd_per_job: float = 5.0
    confirm_above_usd: float = 1.0
    price_in_per_m: float = 0.75
    price_out_per_m: float = 3.75

    # Video token assumptions (documentation values, refined by calibration)
    tokens_per_sec_low: float = 100.0
    tokens_per_sec_high: float = 300.0
    context_tokens: int = 1_000_000
    context_share_max: float = 0.6

    # Calibration results
    url_offsets_ok: bool = False
    url_ts_relative_to_window: bool = True
    calibration_max_cost_usd: float = 1.0

    # Behaviour
    auto_render: bool = False
    subtitles: bool = False
    capcut_export: bool = False
    use_nvenc: bool = False
    ffmpeg_timeout_s: int = 900

    # Storage and services
    data_dir: Path = Path("data")
    retention_days: int = 14
    database_url: str = "sqlite+aiosqlite:///data/cliphunter.db"
    redis_url: str = "redis://localhost:6379/0"

    # Telegram (phase 6)
    telegram_token: SecretStr = SecretStr("")
    telegram_allowed_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    log_level: str = "INFO"

    @field_validator("telegram_allowed_ids", mode="before")
    @classmethod
    def _split_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return [int(x) for x in v.replace(";", ",").split(",") if x.strip()]
        return v

    @field_validator("overlap_s")
    @classmethod
    def _overlap_lt_chunk(cls, v: int, info: object) -> int:
        chunk = getattr(info, "data", {}).get("chunk_s")
        if chunk is not None and v >= chunk:
            raise ValueError("OVERLAP_S must be smaller than CHUNK_S")
        return v

    def tokens_per_sec(self, resolution: MediaRes) -> float:
        return self.tokens_per_sec_low if resolution == "low" else self.tokens_per_sec_high

    def chunk_fits_context(self, resolution: MediaRes) -> bool:
        """Plan S2 check: CHUNK_S * tokens/sec must be below the context share limit."""
        return self.chunk_s * self.tokens_per_sec(resolution) < (
            self.context_tokens * self.context_share_max
        )

    def secrets(self) -> list[str]:
        vals = [self.gemini_api_key.get_secret_value(), self.telegram_token.get_secret_value()]
        return [v for v in vals if v]


@lru_cache
def get_settings() -> Settings:
    return Settings()
