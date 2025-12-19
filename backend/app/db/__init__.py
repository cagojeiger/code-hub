"""Database module for code-hub.

This module provides database connection, models, and utilities.
Uses SQLModel with async SQLite (WAL mode) for the MVP.

Note: Password utilities (hash_password, verify_password) are in app.core.security
"""

from app.db.models import Session, User, Workspace, WorkspaceStatus
from app.db.session import close_db, get_async_session, get_engine, init_db

__all__ = [
    "Session",
    "User",
    "Workspace",
    "WorkspaceStatus",
    "close_db",
    "get_async_session",
    "get_engine",
    "init_db",
]
