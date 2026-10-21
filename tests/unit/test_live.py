"""Unit tests for live stream recording and progressive chunking (plan, requirement F10)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cliphunter.ingest.live import (
    LiveStreamRecorder,
    LiveStreamSession,
    extract_stream_direct_url,
)
from cliphunter.models import Source, SourceKind
from cliphunter.worker.tasks import JobTaskManager


def test_extract_stream_direct_url_file_or_m3u8(tmp_path: Path) -> None:
    test_file = tmp_path / "test.mp4"
    test_file.write_text("dummy")

    assert extract_stream_direct_url(str(test_file)) == str(test_file)
    assert (
        extract_stream_direct_url("http://example.com/live/stream.m3u8")
        == "http://example.com/live/stream.m3u8"
    )


def test_extract_stream_direct_url_ytdlp_fallback() -> None:
    # If yt-dlp fails on non-existent stream, falls back to original url
    res = extract_stream_direct_url("https://example.com/unknown")
    assert res == "https://example.com/unknown"


def test_live_recorder_init(tmp_path: Path) -> None:
    recorder = LiveStreamRecorder(
        url="https://youtube.com/live/test",
        output_dir=tmp_path / "chunks",
        chunk_s=300,
        max_chunks=2,
    )
    assert recorder.url == "https://youtube.com/live/test"
    assert recorder.chunk_s == 300
    assert recorder.max_chunks == 2
    assert recorder.is_running is False


@pytest.mark.asyncio
async def test_live_recorder_watch_segments(tmp_path: Path) -> None:
    out_dir = tmp_path / "chunks"
    out_dir.mkdir(parents=True)

    callback_calls: list[tuple[Path, int]] = []

    async def on_chunk(p: Path, idx: int) -> None:
        callback_calls.append((p, idx))

    recorder = LiveStreamRecorder(
        url="dummy",
        output_dir=out_dir,
        chunk_s=10,
        on_chunk_ready=on_chunk,
        max_chunks=2,
    )

    # Simulate chunk files being created
    f0 = out_dir / "chunk_0000.mp4"
    f0.write_bytes(b"chunk0_data")

    # Start watcher in task
    task = asyncio.create_task(recorder._watch_segments())

    # When second chunk appears, first chunk is considered finished
    await asyncio.sleep(0.1)
    f1 = out_dir / "chunk_0001.mp4"
    f1.write_bytes(b"chunk1_data")

    await asyncio.sleep(1.2)
    recorder._stop_event.set()
    await task

    # At least chunk_0000 should have been reported as ready
    assert len(callback_calls) >= 1
    assert callback_calls[0][1] == 0
    assert callback_calls[0][0].name == "chunk_0000.mp4"


@pytest.mark.asyncio
async def test_live_session_handle_chunk(tmp_path: Path) -> None:
    db = MagicMock()
    mock_session = AsyncMock()
    db.session_factory.return_value.__aenter__.return_value = mock_session

    task_mgr = MagicMock(spec=JobTaskManager)

    chunk_file = tmp_path / "chunk_0000.mp4"
    chunk_file.write_bytes(b"video_bytes")

    session = LiveStreamSession(
        url="https://youtube.com/live/stream",
        session_id="test_sess",
        profile_id="default",
        chunk_s=60,
        auto_yes=True,
        db=db,
        task_mgr=task_mgr,
    )

    fake_source = Source(
        id="file_chunk0",
        kind=SourceKind.FILE,
        path=str(chunk_file),
        duration_s=60.0,
        title="Test segment",
    )

    with (
        patch("cliphunter.ingest.live.probe_file", return_value=fake_source),
        patch("cliphunter.ingest.live.upsert_source", new_callable=AsyncMock) as mock_upsert,
        patch("cliphunter.ingest.live.create_job", new_callable=AsyncMock) as mock_create,
    ):
        await session._handle_new_chunk(chunk_file, 0)
        assert mock_upsert.called
        assert mock_create.called

    assert len(session.created_job_ids) == 1
    assert session.created_job_ids[0] == "job_live_test_sess_000"
    task_mgr.start_job.assert_called_once_with("job_live_test_sess_000")
