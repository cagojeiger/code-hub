"""Database module for code-hub.

This module provides database connection, models, and utilities.
Uses SQLModel with async SQLite (WAL mode) for the MVP.
"""

from app.db.models import Session, User, Workspace, WorkspaceStatus
from app.db.seed import (
    TEST_USER_PASSWORD,
    TEST_USER_USERNAME,
    hash_password,
    seed_database,
    seed_test_user,
    verify_password,
)
from app.db.session import close_db, get_async_session, get_engine, init_db

__all__ = [
    # Models
    "User",
    "Session",
    "Workspace",
    "WorkspaceStatus",
    # Session management
    "init_db",
    "close_db",
    "get_engine",
    "get_async_session",
    # Password utilities
    "hash_password",
    "verify_password",
    # Seeding
    "seed_database",
    "seed_test_user",
    "TEST_USER_USERNAME",
    "TEST_USER_PASSWORD",
]
