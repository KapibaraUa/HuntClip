"""Single entry point for paid Gemini calls (Interactions API; see docs/CALIBRATION.md).

Every call goes through `GeminiVideoClient.call()`: budget check before, usage accounting after,
a JSONL record in api_calls.jsonl (DB table comes in a later phase), and an on-disk cache.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from cliphunter.config import Settings
from cliphunter.llm.budget import Budget, cost_usd
from cliphunter.llm.cache import DiskCache, cache_key
from cliphunter.llm.schemas import json_schema

log = logging.getLogger(__name__)

_RETRY_CODES = {429, 500, 502, 503, 504}


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thought: int = 0
    tokens_tool: int = 0

    @property
    def billed_in(self) -> int:
        # Assumption (DECISIONS.md): tool-use tokens (agentic frames/audio) are billed as input.
        return self.tokens_in + self.tokens_tool

    @property
    def billed_out(self) -> int:
        return self.tokens_out + self.tokens_thought  # thinking is billed as output


@dataclass
class CallResult:
    text: str
    usage: Usage
    cost_usd: float
    latency_ms: int
    cache_hit: bool


def _is_retryable(exc: BaseException) -> bool:
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return isinstance(code, int) and code in _RETRY_CODES


def usage_from_interaction(interaction: Any) -> Usage:
    u = getattr(interaction, "usage", None)

    def g(name: str) -> int:
        return int(getattr(u, name, 0) or 0)

    return Usage(
        tokens_in=g("total_input_tokens"),
        tokens_out=g("total_output_tokens"),
        tokens_thought=g("total_thought_tokens"),
        tokens_tool=g("total_tool_use_tokens"),
    )


class GeminiVideoClient:
    def __init__(
        self,
        settings: Settings,
        budget: Budget | None = None,
        cache: DiskCache | None = None,
        sdk: Any = None,
        log_path: Path | None = None,
    ) -> None:
        self.settings = settings
        self.budget = budget or Budget(settings.max_cost_usd_per_job)
        self.cache = cache or DiskCache(settings.data_dir / "cache" / "llm")
        self.log_path = log_path or settings.data_dir / "logs" / "api_calls.jsonl"
        self._sdk = sdk
        self.job_id: str | None = None

    @property
    def sdk(self) -> Any:
        if self._sdk is None:
            key = self.settings.gemini_api_key.get_secret_value()
            if not key:
                raise RuntimeError("GEMINI_API_KEY is empty; put it in .env")
            from google import genai

            self._sdk = genai.Client(api_key=key)
        return self._sdk

    # --- Files API -------------------------------------------------------------------------
    def upload(self, path: Path, poll_s: float = 5.0, timeout_s: float = 900.0) -> tuple[str, str]:
        """Upload to the Files API and wait until ACTIVE. Returns (uri, mime_type)."""
        f = self.sdk.files.upload(file=str(path))
        deadline = time.monotonic() + timeout_s
        while getattr(f.state, "name", str(f.state)) == "PROCESSING":
            if time.monotonic() > deadline:
                raise TimeoutError(f"Files API processing timed out for {path}")
            time.sleep(poll_s)
            f = self.sdk.files.get(name=f.name)
        if getattr(f.state, "name", str(f.state)) != "ACTIVE":
            raise RuntimeError(f"Files API upload failed, state={f.state}")
        return str(f.uri), str(f.mime_type)

    # --- Paid call -------------------------------------------------------------------------
    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=2, max=60),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _create(self, body: dict[str, Any]) -> Any:
        return self.sdk.interactions.create(**body)

    def call(
        self,
        *,
        stage: str,
        mode: str,
        video: dict[str, Any] | list[dict[str, Any]],
        prompt: str,
        system: str | None = None,
        schema: type[BaseModel] | None = None,
        thinking_level: str | None = None,
        estimated_cost_usd: float = 0.0,
        cache_parts: dict[str, Any] | None = None,
    ) -> CallResult:
        model = self.settings.gemini_model
        thinking = thinking_level or self.settings.thinking_level
        key = cache_key(
            model=model,
            video=video,
            prompt=prompt,
            system=system,
            schema=schema.__name__ if schema else None,
            thinking=thinking,
            **(cache_parts or {}),
        )
        hit = self.cache.get(key)
        if hit is not None:
            res = CallResult(hit["text"], Usage(**hit["usage"]), 0.0, 0, True)
            self._record(stage, mode, model, res, error=None)
            return res

        self.budget.check(estimated_cost_usd)

        input_parts: list[dict[str, Any]] = (
            [*video, {"type": "text", "text": prompt}]
            if isinstance(video, list)
            else [video, {"type": "text", "text": prompt}]
        )
        body: dict[str, Any] = {
            "model": model,
            "input": input_parts,  # text after video(s) (docs)
            "generation_config": {"thinking_level": thinking},
        }
        if system:
            body["system_instruction"] = system
        if schema is not None:
            body["response_format"] = {
                "type": "text",
                "mime_type": "application/json",
                "schema": json_schema(schema),
            }

        t0 = time.monotonic()
        try:
            interaction = self._create(body)
        except Exception as exc:
            self._record(
                stage,
                mode,
                model,
                None,
                error=repr(exc),
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
            raise
        latency = int((time.monotonic() - t0) * 1000)

        usage = usage_from_interaction(interaction)
        cost = cost_usd(usage.billed_in, usage.billed_out, self.settings)
        self.budget.add(cost)
        text = str(interaction.output_text or "")
        res = CallResult(text, usage, cost, latency, False)
        self.cache.put(key, {"text": text, "usage": asdict(usage)})
        self._record(stage, mode, model, res, error=None)
        return res

    def _record(
        self,
        stage: str,
        mode: str,
        model: str,
        res: CallResult | None,
        error: str | None,
        latency_ms: int = 0,
    ) -> None:
        rec: dict[str, Any] = {
            "ts": time.time(),
            "job_id": self.job_id,
            "stage": stage,
            "model": model,
            "mode": mode,
            "error": error,
            "tokens_in": res.usage.billed_in if res else 0,
            "tokens_out": res.usage.billed_out if res else 0,
            "tokens_thought": res.usage.tokens_thought if res else 0,
            "cost_usd": res.cost_usd if res else 0.0,
            "latency_ms": res.latency_ms if res else latency_ms,
            "cache_hit": res.cache_hit if res else False,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log.info("api_call", extra={k: v for k, v in rec.items() if k != "job_id"})
