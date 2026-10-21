"""Database session management and engine configuration."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from cliphunter.config import Settings
from cliphunter.db.models import Base

log = logging.getLogger(__name__)


def normalize_db_url(url: str, data_dir: Path | None = None) -> str:
    """Normalizes database URLs to async dialects (asyncpg for postgres, aiosqlite for sqlite)."""
    if not url or url.strip() == "":
        db_path = (data_dir or Path("data")) / "cliphunter.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db_path.as_posix()}"

    # Convert sync or psycopg postgres URLs to asyncpg
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql+asyncpg://")
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://")

    # Convert sync sqlite to aiosqlite
    if url.startswith("sqlite:///") and not url.startswith("sqlite+aiosqlite:///"):
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///")

    if url.startswith("sqlite+aiosqlite:///"):
        raw_path = url.replace("sqlite+aiosqlite:///", "")
        if raw_path != ":memory:":
            Path(raw_path).parent.mkdir(parents=True, exist_ok=True)

    return url


class Database:
    """Async database manager."""

    def __init__(self, url: str) -> None:
        self.url = normalize_db_url(url)
        self.engine: AsyncEngine = create_async_engine(
            self.url,
            echo=False,
            future=True,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def init_models(self) -> None:
        """Creates tables if they do not exist."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        log.info("Database initialized with URL: %s", self.url.split("@")[-1])

    async def close(self) -> None:
        """Disposes connection pool."""
        await self.engine.dispose()

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Dependency for FastAPI endpoints."""
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


# Global database instance
_global_db: Database | None = None


def get_db_manager(settings: Settings | None = None) -> Database:
    global _global_db
    if _global_db is None:
        s = settings or Settings()
        _global_db = Database(s.database_url)
    return _global_db


def set_db_manager(db: Database) -> None:
    global _global_db
    _global_db = db


async def init_db(settings: Settings | None = None) -> None:
    """Initializes tables for current database engine."""
    db = get_db_manager(settings)
    await db.init_models()


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Returns the async session factory from the active DB manager."""
    return get_db_manager().session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends(get_db) function."""
    db = get_db_manager()
    async for session in db.get_session():
        yield session
