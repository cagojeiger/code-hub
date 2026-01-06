"""Proxy authentication with TTL caching (3s TTL, 1000 maxsize)."""

from cachetools_async import cached
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codehub.core.errors import (
    ForbiddenError,
    UnauthorizedError,
    WorkspaceNotFoundError,
)
from codehub.core.models import Workspace
from codehub.infra.cache import (
    clear_session_cache,
    clear_workspace_cache,
    session_cache,
    workspace_cache,
)
from codehub.services.session_service import SessionService


def _session_key(_db: AsyncSession, session_cookie: str | None) -> str | None:
    return session_cookie


def _workspace_key(
    _db: AsyncSession, workspace_id: str, user_id: str
) -> tuple[str, str]:
    return (workspace_id, user_id)


@cached(cache=session_cache, key=_session_key)
async def get_user_id_from_session(
    db: AsyncSession, session_cookie: str | None
) -> str:
    """Get user ID from session cookie. Raises UnauthorizedError if invalid."""
    if session_cookie is None:
        raise UnauthorizedError()

    session = await SessionService.get_valid(db, session_cookie)
    if session is None:
        raise UnauthorizedError()

    return session.user_id


@cached(cache=workspace_cache, key=_workspace_key)
async def get_workspace_for_user(
    db: AsyncSession, workspace_id: str, user_id: str
) -> Workspace:
    """Get workspace and verify ownership. Raises WorkspaceNotFoundError/ForbiddenError."""
    result = await db.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,  # type: ignore[arg-type]
            Workspace.deleted_at.is_(None),  # type: ignore[union-attr]
        )
    )
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFoundError()

    if workspace.owner_user_id != user_id:
        raise ForbiddenError("You don't have access to this workspace")

    return workspace


__all__ = [
    "get_user_id_from_session",
    "get_workspace_for_user",
    "clear_session_cache",
    "clear_workspace_cache",
]
