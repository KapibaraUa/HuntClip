"""FastAPI Application factory for ClipHunter.

Mounts all API routers, CORS, media streaming, and the Review Web UI.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from cliphunter.api.routes_candidates import router as candidates_router
from cliphunter.api.routes_clips import router as clips_router
from cliphunter.api.routes_jobs import router as jobs_router
from cliphunter.api.routes_media import router as media_router
from cliphunter.api.routes_profiles import router as profiles_router
from cliphunter.api.routes_system import router as system_router
from cliphunter.api.routes_uploads import router as uploads_router
from cliphunter.config import Settings
from cliphunter.db.session import init_db

log = logging.getLogger("cliphunter.api")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """App lifespan context to initialize DB schema and directories."""
    settings = Settings()
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "uploads").mkdir(parents=True, exist_ok=True)
    (data_dir / "jobs").mkdir(parents=True, exist_ok=True)

    # Initialize DB tables if SQLite standalone or first boot
    try:
        await init_db()
    except Exception as exc:
        log.warning("Database init warning: %s", exc)

    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="ClipHunter",
        description="Autonomous AI highlight clip discovery & rendering engine for live streams",
        version="0.3.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API routes
    app.include_router(uploads_router)
    app.include_router(jobs_router)
    app.include_router(candidates_router)
    app.include_router(clips_router)
    app.include_router(profiles_router)
    app.include_router(media_router)
    app.include_router(system_router)

    # Serve static files and Single Page App (Web UI)
    if WEB_DIR.exists():
        static_dir = WEB_DIR / "static"
        if static_dir.exists():
            app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        async def serve_index() -> FileResponse:
            index_path = WEB_DIR / "index.html"
            return FileResponse(str(index_path))

    return app


app = create_app()
