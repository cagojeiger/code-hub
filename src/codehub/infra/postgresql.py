"""Database session management."""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from codehub.app.config import get_settings

logger = logging.getLogger(__name__)

_engine = None
_session_factory = None


async def init_db() -> None:
    global _engine, _session_factory

    settings = get_settings()
    url = str(settings.database.url)

    _engine = create_async_engine(
        url,
        echo=settings.database.echo,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        pool_recycle=3600,
        pool_pre_ping=True,
        poolclass=AsyncAdaptedQueuePool,
    )

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    try:
        async with _engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info(
            "PostgreSQL connected",
            extra={
                "event": "db_connected",
                "pool_size": settings.database.pool_size,
                "max_overflow": settings.database.max_overflow,
            },
        )
    except Exception as e:
        logger.error(
            "PostgreSQL connection failed",
            extra={
                "event": "db_error",
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        raise


async def close_db() -> None:
    global _engine, _session_factory

    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def get_engine():
    if _engine is None:
        raise RuntimeError("Database not initialized")
    return _engine


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if _session_factory is None:
        raise RuntimeError("Database not initialized")

    async with _session_factory() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get session factory for creating new sessions.

    Use for long-lived connections (SSE, WebSocket) where fresh sessions are needed.
    """
    if _session_factory is None:
        raise RuntimeError("Database not initialized")
    return _session_factory
