"""HTTP 206 Partial Content / Range header media streaming endpoints.

Allows smooth HTML5 video player seeking for source proxies and rendered clips.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from cliphunter.db.repo import get_clip, get_job
from cliphunter.db.session import get_db

router = APIRouter(prefix="/api/media", tags=["media"])

CHUNK_SIZE = 1024 * 1024  # 1 MB


async def _file_chunk_generator(
    file_path: Path, start: int, length: int, chunk_size: int = CHUNK_SIZE
) -> AsyncGenerator[bytes, None]:
    """Asynchronously reads and yields chunks of a file range."""
    bytes_remaining = length
    with open(file_path, "rb") as f:
        f.seek(start)
        while bytes_remaining > 0:
            to_read = min(chunk_size, bytes_remaining)
            chunk = f.read(to_read)
            if not chunk:
                break
            bytes_remaining -= len(chunk)
            yield chunk


def stream_video_file(request: Request, path: Path) -> Response:
    """Builds an HTTP 206 (or 200) streaming response supporting Range headers."""
    if not path.exists() or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Media file not found on disk"
        )

    file_size = path.stat().st_size
    range_header = request.headers.get("Range")

    if not range_header:
        # Full content response (200)
        return StreamingResponse(
            _file_chunk_generator(path, 0, file_size),
            status_code=status.HTTP_200_OK,
            headers={
                "Content-Length": str(file_size),
                "Content-Type": "video/mp4",
                "Accept-Ranges": "bytes",
            },
        )

    # Parse Range: bytes=start-end
    try:
        range_val = range_header.strip().replace("bytes=", "")
        parts = range_val.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else file_size - 1
    except (ValueError, IndexError) as err:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Invalid range header",
        ) from err

    if start >= file_size or end >= file_size or start > end:
        return Response(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    content_length = (end - start) + 1
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": "video/mp4",
    }

    return StreamingResponse(
        _file_chunk_generator(path, start, content_length),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        headers=headers,
    )


@router.get("/proxy/{job_id}")
async def stream_job_proxy(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Streams the 720p proxy video (or original video) for the job with seeking support."""
    job = await get_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    video_path_str = job.proxy_path or job.original_path
    if not video_path_str:
        raise HTTPException(
            status_code=404,
            detail="No proxy or original video available for this job yet",
        )

    path = Path(video_path_str)
    return stream_video_file(request, path)


@router.get("/clips/{clip_id}")
async def stream_clip(
    clip_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Streams a rendered highlight clip with seeking support."""
    clip = await get_clip(session, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    path = Path(clip.path)
    return stream_video_file(request, path)
