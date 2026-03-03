from datetime import UTC, datetime
from typing import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from codehub.core.domain import DesiredState, Operation, Phase
from codehub.core.models import Workspace


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.limits.max_running_per_user = 3
    settings.runtime.default_image = "default:latest"
    settings.runtime.resource_prefix = "codehub-ws-"
    return settings


@pytest.fixture
def make_workspace() -> Callable[..., Workspace]:
    def _make_workspace(
        id: str = "ws-1",
        owner_user_id: str = "user-1",
        name: str = "Test Workspace",
        description: str | None = "Test Description",
        memo: str | None = None,
        image_ref: str = "ubuntu:22.04",
        instance_backend: str = "local-docker",
        storage_backend: str = "minio",
        home_store_key: str = "codehub-ws-ws-1-home",
        conditions: dict[str, object] | None = None,
        phase: Phase | str = Phase.PENDING,
        operation: Operation | str = Operation.NONE,
        desired_state: DesiredState | str = DesiredState.RUNNING,
        archive_key: str | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        last_access_at: datetime | None = None,
        deleted_at: datetime | None = None,
    ) -> Workspace:
        now = datetime.now(UTC)
        return Workspace(
            id=id,
            owner_user_id=owner_user_id,
            name=name,
            description=description,
            memo=memo,
            image_ref=image_ref,
            instance_backend=instance_backend,
            storage_backend=storage_backend,
            home_store_key=home_store_key,
            conditions=conditions or {},
            phase=Phase(phase) if isinstance(phase, str) else phase,
            operation=Operation(operation) if isinstance(operation, str) else operation,
            desired_state=(
                DesiredState(desired_state)
                if isinstance(desired_state, str)
                else desired_state
            ),
            archive_key=archive_key,
            created_at=created_at or now,
            updated_at=updated_at or now,
            last_access_at=last_access_at or now,
            deleted_at=deleted_at,
        )

    return _make_workspace


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch: pytest.MonkeyPatch, mock_settings: MagicMock) -> None:
    import codehub.services.workspace_service as workspace_service

    monkeypatch.setattr(workspace_service, "get_settings", lambda: mock_settings)
    monkeypatch.setattr(workspace_service, "_settings", mock_settings)
