"""Authentication and authorization helpers for workspace proxy."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codehub.core.errors import (
    ForbiddenError,
    UnauthorizedError,
    WorkspaceNotFoundError,
)
from codehub.core.models import Workspace
from codehub.services.session_service import SessionService


async def get_user_id_from_session(
    db: AsyncSession, session_cookie: str | None
) -> str:
    """Get user ID from session cookie. Raises UnauthorizedError if invalid."""
    if session_cookie is None:
        raise UnauthorizedError()

    result = await SessionService.get_valid_with_user(db, session_cookie)
    if result is None:
        raise UnauthorizedError()

    _, user = result
    return user.id


async def get_workspace_for_user(
    session: AsyncSession, workspace_id: str, user_id: str
) -> Workspace:
    """Get workspace by ID and verify owner. Raises appropriate errors."""
    result = await session.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,  # type: ignore[arg-type]
            Workspace.deleted_at.is_(None),  # type: ignore[union-attr]
        )
    )
    workspace = result.scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFoundError()

    # Verify owner
    if workspace.owner_user_id != user_id:
        raise ForbiddenError("You don't have access to this workspace")

    return workspace
