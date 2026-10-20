"""Structured JSON logging with a job_id field and secret masking."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

job_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("job_id", default=None)

# Generic patterns: Google API keys, "key=..." query params, bearer tokens, Telegram bot tokens.
_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?i)(key|token|api_key)=([^&\s\"']+)"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"\b\d{6,}:[A-Za-z0-9_\-]{30,}\b"),
]
MASK = "***"


def mask(text: str, secrets: Iterable[str] = ()) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, MASK)
    text = _PATTERNS[1].sub(lambda m: f"{m.group(1)}={MASK}", text)
    for p in (_PATTERNS[0], _PATTERNS[2], _PATTERNS[3]):
        text = p.sub(MASK, text)
    return text


class JsonFormatter(logging.Formatter):
    _STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = list(secrets)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "job_id": job_id_var.get(),
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in self._STD:
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Mask on the final serialized form so secrets in any field are caught.
        return mask(json.dumps(payload, ensure_ascii=False, default=str), self._secrets)


def setup_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(secrets))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


def set_job_id(job_id: str | None) -> contextvars.Token[str | None]:
    return job_id_var.set(job_id)
