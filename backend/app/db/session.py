"""Database session management for PostgreSQL.

This module provides async database connection and session management.
"""

import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_engine: "AsyncEngine | None" = None


def _get_engine(database_url: str, echo: bool = False) -> "AsyncEngine":
    """Create async engine with PostgreSQL-optimized settings."""
    return create_async_engine(
        database_url,
        echo=echo,
        pool_size=10,
        max_overflow=20,
        pool_recycle=3600,
        pool_pre_ping=True,
        connect_args={
            "command_timeout": 30,
            "server_settings": {
                "statement_timeout": "30s",
                "lock_timeout": "10s",
                "application_name": "codehub-backend",
            },
        },
    )


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

    _engine = _get_engine(database_url, echo)

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
