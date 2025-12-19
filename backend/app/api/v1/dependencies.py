"""API v1 dependencies for code-hub.

Contains shared dependencies for API endpoints.
Auth is not implemented yet (M6), so we use a test user.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from app.core.config import get_settings
from app.db import User, get_async_session
from app.services.instance.interface import InstanceController
from app.services.instance.local_docker import LocalDockerInstanceController
from app.services.storage.interface import StorageProvider
from app.services.storage.local_dir import LocalDirStorageProvider
from app.services.workspace_service import WorkspaceService

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


@lru_cache
def get_storage_provider() -> StorageProvider:
    """Get storage provider singleton based on config."""
    settings = get_settings()
    if settings.home_store.backend == "local-dir":
        return LocalDirStorageProvider(
            control_plane_base_dir=settings.home_store.control_plane_base_dir,
            workspace_base_dir=settings.home_store.workspace_base_dir,  # type: ignore[arg-type]
        )
    raise ValueError(f"Unsupported storage backend: {settings.home_store.backend}")


@lru_cache
def get_instance_controller() -> InstanceController:
    """Get instance controller singleton."""
    return LocalDockerInstanceController()


@lru_cache
def get_workspace_service() -> WorkspaceService:
    """Get workspace service singleton."""
    return WorkspaceService(
        storage=get_storage_provider(),
        instance=get_instance_controller(),
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_async_session)]
Storage = Annotated[StorageProvider, Depends(get_storage_provider)]
Instance = Annotated[InstanceController, Depends(get_instance_controller)]
WsService = Annotated[WorkspaceService, Depends(get_workspace_service)]
