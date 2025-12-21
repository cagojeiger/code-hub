"""Database session management with SQLite WAL mode.

This module provides async database connection and session management.
SQLite WAL (Write-Ahead Logging) mode is enabled for better concurrency.
"""

import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_engine: "AsyncEngine | None" = None


def _get_engine(database_url: str, echo: bool = False) -> "AsyncEngine":
    """Create async engine with appropriate settings."""
    connect_args: dict[str, Any] = {}

    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

        if ":memory:" in database_url or "mode=memory" in database_url:
            return create_async_engine(
                database_url,
                echo=echo,
                connect_args=connect_args,
                poolclass=StaticPool,
            )

    return create_async_engine(
        database_url,
        echo=echo,
        connect_args=connect_args,
    )


async def _enable_wal_mode(engine: "AsyncEngine") -> None:
    """Enable WAL mode for SQLite databases."""
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        logger.info("SQLite WAL mode enabled")


async def init_db(
    database_url: str, echo: bool = False, create_tables: bool = True
) -> "AsyncEngine":
    """Initialize database connection and optionally create tables.

    Args:
        database_url: Database connection URL
        echo: Enable SQL query logging
        create_tables: Create tables using SQLModel metadata (default True).
                      Set to False when using Alembic for migrations.
    """
    global _engine

    is_sqlite = database_url.startswith("sqlite")
    is_memory = ":memory:" in database_url or "mode=memory" in database_url

    if is_sqlite and not is_memory:
        db_path = database_url.split("///")[-1]
        if db_path.startswith("./"):
            db_path = db_path[2:]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    _engine = _get_engine(database_url, echo)

    # Enable WAL mode for file-based SQLite only
    if is_sqlite and not is_memory:
        await _enable_wal_mode(_engine)

    # Create tables if requested (skip when using Alembic)
    if create_tables:
        async with _engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    logger.info("Database initialized: %s", database_url.split("@")[-1])
    return _engine


async def close_db() -> None:
    """Close database connection."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
        logger.info("Database connection closed")


def get_engine() -> "AsyncEngine":
    """Get the current database engine."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine


async def get_async_session() -> AsyncGenerator[AsyncSession]:
    """Get async database session."""
    engine = get_engine()
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session
