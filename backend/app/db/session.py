"""Database session management with SQLite WAL mode.

This module provides async database connection and session management.
SQLite WAL (Write-Ahead Logging) mode is enabled for better concurrency.
"""

import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_engine: "AsyncEngine | None" = None


def _get_engine(database_url: str, echo: bool = False) -> "AsyncEngine":
    """Create async engine with appropriate settings.

    For SQLite, uses StaticPool for in-memory databases
    and enables WAL mode for file-based databases.
    """
    connect_args: dict = {}

    if database_url.startswith("sqlite"):
        # Enable foreign keys for SQLite
        connect_args["check_same_thread"] = False

        # Use StaticPool for in-memory databases (testing)
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
    """Enable WAL mode for SQLite databases.

    WAL (Write-Ahead Logging) provides better concurrency:
    - Readers don't block writers
    - Writers don't block readers
    - Better crash recovery
    """
    async with engine.begin() as conn:
        # Enable WAL mode
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        # Enable foreign keys
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        logger.info("SQLite WAL mode enabled")


async def init_db(database_url: str, echo: bool = False) -> "AsyncEngine":
    """Initialize database connection and create tables.

    Args:
        database_url: Database connection URL
        echo: Whether to echo SQL statements

    Returns:
        Configured AsyncEngine instance
    """
    global _engine

    # Ensure data directory exists for file-based SQLite
    if database_url.startswith("sqlite") and ":memory:" not in database_url:
        # Extract file path from URL (e.g., sqlite+aiosqlite:///./data/codehub.db)
        db_path = database_url.split("///")[-1]
        if db_path.startswith("./"):
            db_path = db_path[2:]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    _engine = _get_engine(database_url, echo)

    # Enable WAL mode for SQLite (skip for in-memory)
    if (
        database_url.startswith("sqlite")
        and ":memory:" not in database_url
        and "mode=memory" not in database_url
    ):
        await _enable_wal_mode(_engine)

    # Create all tables
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
    """Get the current database engine.

    Returns:
        Current AsyncEngine instance

    Raises:
        RuntimeError: If database is not initialized
    """
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Get async database session.

    Yields:
        AsyncSession for database operations

    Example:
        async for session in get_async_session():
            result = await session.execute(select(User))
    """
    engine = get_engine()
    async_session = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session() as session:
        yield session
