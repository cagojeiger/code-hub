"""API v1 dependencies for code-hub.

Contains shared dependencies for API endpoints.
Auth is not implemented yet (M6), so we use a test user.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.db import User, get_async_session

INITIAL_ADMIN_USERNAME = "admin"


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> User:
    """Get current user (test user for now, auth in M6)."""
    result = await session.execute(
        select(User).where(col(User.username) == INITIAL_ADMIN_USERNAME)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise RuntimeError("Initial admin user not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_async_session)]
