"""File upload API endpoints."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from cliphunter.config import Settings
from cliphunter.db.repo import upsert_source
from cliphunter.db.session import get_db
from cliphunter.ingest.probe import probe_source
from cliphunter.models import Source, SourceKind

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("")
async def upload_file(
    file: UploadFile,
    session: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Uploads a local stream video file and registers it as a source."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename required")

    settings = Settings()
    upload_dir = Path(settings.data_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    dest_file = upload_dir / file.filename

    # Stream write to disk
    h = hashlib.sha256()
    with dest_file.open("wb") as buffer:
        while chunk := await file.read(1024 * 1024):  # 1MB chunks
            h.update(chunk)
            buffer.write(chunk)

    sha256 = h.hexdigest()

    # Probe source metadata via ffprobe
    try:
        source_meta = probe_source(str(dest_file))
    except Exception:
        source_meta = Source(
            id=f"src_{sha256[:12]}",
            kind=SourceKind.FILE,
            path=str(dest_file),
            duration_s=0.0,
            title=file.filename,
            channel="Local Upload",
            sha256=sha256,
        )

    source_meta.sha256 = sha256
    source_orm = await upsert_source(session, source_meta)
    await session.commit()

    return {
        "source_id": source_orm.id,
        "filename": file.filename,
        "title": source_orm.title,
        "duration_s": source_orm.duration_s,
        "path": source_orm.path,
        "sha256": source_orm.sha256,
    }
