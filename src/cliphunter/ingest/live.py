"""Live stream recording and progressive chunk-by-chunk analysis (plan, requirement F10)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import subprocess
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from cliphunter.config import Settings, get_settings
from cliphunter.db.repo import create_job, upsert_source
from cliphunter.db.session import Database, get_db_manager
from cliphunter.ingest.files import probe_file
from cliphunter.ingest.youtube import ytdlp_base
from cliphunter.models import Job, JobParams, Profile
from cliphunter.worker.tasks import JobTaskManager
from cliphunter.worker.tasks import task_manager as default_task_manager

log = logging.getLogger(__name__)


def extract_stream_direct_url(url: str, timeout_s: int = 60) -> str:
    """Extracts direct stream URL (HLS / m3u8 / media stream) using yt-dlp.

    If url is a local file or direct stream link, returns it directly.
    """
    clean_url = url.strip()
    if Path(clean_url).exists() or clean_url.endswith((".m3u8", ".mpd", ".ts")):
        return clean_url

    cmd = [*ytdlp_base(), "-g", clean_url]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=timeout_s)
        direct_url = proc.stdout.strip().splitlines()[0]
        return direct_url
    except Exception as exc:
        log.warning(
            "yt-dlp could not extract direct stream URL for %s: %s; using original URL", url, exc
        )
        return clean_url


class LiveStreamRecorder:
    """Records a live stream and segments it into discrete chunks of chunk_s duration."""

    def __init__(
        self,
        url: str,
        output_dir: Path,
        chunk_s: int = 2700,
        on_chunk_ready: Callable[[Path, int], Awaitable[None]] | None = None,
        max_chunks: int | None = None,
    ) -> None:
        self.url = url
        self.output_dir = output_dir
        self.chunk_s = chunk_s
        self.on_chunk_ready = on_chunk_ready
        self.max_chunks = max_chunks
        self._proc: asyncio.subprocess.Process | None = None
        self._watcher_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._recorded_chunks: list[Path] = []

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        """Starts recording the stream and background segment monitoring."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()

        # Resolve direct stream URL if possible
        stream_target = await asyncio.to_thread(extract_stream_direct_url, self.url)

        segment_template = str(self.output_dir / "chunk_%04d.mp4")
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            stream_target,
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(self.chunk_s),
            "-reset_timestamps",
            "1",
            segment_template,
        ]

        log.info("Launching live stream ffmpeg segmentation: %s", " ".join(ffmpeg_cmd))
        self._proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._watcher_task = asyncio.create_task(self._watch_segments())

    async def stop(self) -> None:
        """Gracefully stops the recorder and finishes segment processing."""
        self._stop_event.set()
        if self._proc and self._proc.returncode is None:
            log.info("Terminating live stream recording process")
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except Exception:
                with contextlib.suppress(Exception):
                    self._proc.kill()

        if self._watcher_task and not self._watcher_task.done():
            await self._watcher_task

    async def _watch_segments(self) -> None:
        """Monitors the output directory for completed segment files."""
        processed_indices: set[int] = set()

        while not self._stop_event.is_set():
            if self._proc is not None and self._proc.returncode is not None:
                # Process exited
                break

            await asyncio.sleep(1.0)
            files = sorted(self.output_dir.glob("chunk_*.mp4"))
            if len(files) > 1:
                # All files except the last one currently being written are completed
                for f in files[:-1]:
                    idx = int(f.stem.replace("chunk_", ""))
                    if idx not in processed_indices:
                        processed_indices.add(idx)
                        self._recorded_chunks.append(f)
                        log.info("Completed live segment %d: %s", idx, f.name)
                        if self.on_chunk_ready:
                            await self.on_chunk_ready(f, idx)

                        if self.max_chunks and len(processed_indices) >= self.max_chunks:
                            log.info("Max chunks limit (%d) reached. Stopping.", self.max_chunks)
                            self._stop_event.set()
                            if self._proc:
                                self._proc.terminate()
                            return

        # Final pass after process exit or stop
        await asyncio.sleep(0.5)
        files = sorted(self.output_dir.glob("chunk_*.mp4"))
        for f in files:
            try:
                idx = int(f.stem.replace("chunk_", ""))
            except ValueError:
                continue
            if idx not in processed_indices and f.stat().st_size > 0:
                processed_indices.add(idx)
                self._recorded_chunks.append(f)
                log.info("Completed final live segment %d: %s", idx, f.name)
                if self.on_chunk_ready:
                    await self.on_chunk_ready(f, idx)


class LiveStreamSession:
    """Orchestrates recording a live stream and progressively running pipeline jobs per segment."""

    def __init__(
        self,
        url: str,
        session_id: str | None = None,
        profile_id: str = "default",
        chunk_s: int = 2700,
        auto_yes: bool = False,
        max_chunks: int | None = None,
        db: Database | None = None,
        task_mgr: JobTaskManager | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.url = url
        self.session_id = session_id or f"live_{uuid.uuid4().hex[:8]}"
        self.profile_id = profile_id
        self.chunk_s = chunk_s
        self.auto_yes = auto_yes
        self.max_chunks = max_chunks
        self.db = db or get_db_manager()
        self.task_mgr = task_mgr or default_task_manager
        self.settings = settings or get_settings()

        self.session_dir = Path(self.settings.data_dir) / "live" / self.session_id
        self.chunks_dir = self.session_dir / "chunks"

        self.created_job_ids: list[str] = []
        self.recorder = LiveStreamRecorder(
            url=self.url,
            output_dir=self.chunks_dir,
            chunk_s=self.chunk_s,
            on_chunk_ready=self._handle_new_chunk,
            max_chunks=self.max_chunks,
        )

    async def _handle_new_chunk(self, chunk_path: Path, chunk_idx: int) -> None:
        """Invoked when a new live segment finishes recording; launches pipeline job for it."""
        log.info(
            "Live session %s: processing segment #%d (%s)",
            self.session_id,
            chunk_idx,
            chunk_path.name,
        )
        source = probe_file(chunk_path)
        source.title = f"Live stream segment #{chunk_idx + 1} ({self.session_id})"

        job_id = f"job_live_{self.session_id}_{chunk_idx:03d}"
        job = Job(
            id=job_id,
            source=source,
            profile=Profile(name=self.profile_id),
            params=JobParams(
                clip_min_s=self.settings.clip_min_s,
                clip_max_s=self.settings.clip_max_s,
                max_cost_usd=self.settings.max_cost_usd_per_job,
                auto_yes=self.auto_yes,
            ),
        )

        async with self.db.session_factory() as session:
            await upsert_source(session, source)
            await create_job(session, job)
            await session.commit()

        self.created_job_ids.append(job_id)
        # Launch pipeline execution for this segment in the background
        self.task_mgr.start_job(job_id)

    async def run(self) -> list[str]:
        """Runs the live recording session until interrupted or max_chunks reached."""
        log.info("Starting live session %s for %s", self.session_id, self.url)
        await self.recorder.start()

        # Wait until recorder stops
        while self.recorder.is_running and not self.recorder._stop_event.is_set():
            await asyncio.sleep(0.5)

        await self.recorder.stop()
        log.info(
            "Live session %s completed. Created %d segment jobs.",
            self.session_id,
            len(self.created_job_ids),
        )
        return self.created_job_ids

    async def stop(self) -> None:
        """Manually stop the live session."""
        await self.recorder.stop()
