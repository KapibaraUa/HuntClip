"""Server-Sent Events (SSE) manager for live pipeline progress."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from sse_starlette.sse import ServerSentEvent  # type: ignore[attr-defined]

log = logging.getLogger(__name__)


class JobEventBroadcaster:
    """Manages active SSE subscriber queues per job_id."""

    def __init__(self) -> None:
        self._listeners: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, job_id: str) -> AsyncGenerator[ServerSentEvent, None]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self._lock:
            if job_id not in self._listeners:
                self._listeners[job_id] = set()
            self._listeners[job_id].add(queue)

        try:
            # Yield initial connection ping
            yield ServerSentEvent(
                data=json.dumps({"event": "connected", "job_id": job_id}),
                event="ping",
            )
            while True:
                payload = await queue.get()
                event_name = payload.get("event", "message")
                yield ServerSentEvent(
                    data=json.dumps(payload, ensure_ascii=False),
                    event=event_name,
                )
        except asyncio.CancelledError:
            pass
        finally:
            async with self._lock:
                if job_id in self._listeners:
                    self._listeners[job_id].discard(queue)
                    if not self._listeners[job_id]:
                        del self._listeners[job_id]

    async def broadcast(self, job_id: str, event_type: str, data: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._listeners.get(job_id, set()))

        if not queues:
            return

        payload = {"event": event_type, "job_id": job_id, **data}
        for q in queues:
            await q.put(payload)


# Global broadcaster
broadcaster = JobEventBroadcaster()
