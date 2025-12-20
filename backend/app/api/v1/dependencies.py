"""API v1 dependencies for code-hub.

Contains shared dependencies for API endpoints.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Cookie, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import UnauthorizedError
from app.db import User, get_async_session
from app.services.instance.interface import InstanceController
from app.services.instance.local_docker import LocalDockerInstanceController
from app.services.session_service import SessionService
from app.services.storage.interface import StorageProvider
from app.services.storage.local_dir import LocalDirStorageProvider
from app.services.workspace_service import WorkspaceService


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_async_session)],
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> User:
    """Get current authenticated user from session cookie.

    Raises:
        UnauthorizedError: If no session cookie or session is invalid/expired
    """
    if session is None:
        raise UnauthorizedError()

    result = await SessionService.get_valid_with_user(db, session)
    if result is None:
        raise UnauthorizedError()

    _, user = result
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
    settings = get_settings()
    return LocalDockerInstanceController(
        container_prefix=settings.workspace.container_prefix,
        network_name=settings.workspace.network_name,
    )


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
