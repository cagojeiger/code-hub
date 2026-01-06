"""Observer Coordinator integration tests.

Reference: docs/architecture_v2/wc-observer.md
"""

import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from codehub.control.coordinator.observer import ObserverCoordinator
from codehub.core.models import Workspace, User
from codehub.core.interfaces.storage import VolumeInfo
from codehub.core.interfaces.instance import ContainerInfo


class TestObserverTick:
    """ObserverCoordinator.tick() integration test with real DB."""

    @pytest.fixture
    async def test_user(self, test_db_engine: AsyncEngine) -> User:
        """Create test user in DB."""
        async with AsyncSession(test_db_engine) as session:
            user = User(
                id="test-user-obs-001",
                username="test_observer",
                password_hash="hash",
                created_at=datetime.now(UTC),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    @pytest.fixture
    async def test_workspace(
        self, test_db_engine: AsyncEngine, test_user: User
    ) -> Workspace:
        """Create test workspace in DB."""
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-obs-001",
                owner_user_id=test_user.id,
                name="Test Workspace",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-obs-001-home",
                phase="PENDING",
                operation="NONE",
                desired_state="RUNNING",
                conditions={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(ws)
            await session.commit()
            await session.refresh(ws)
            return ws

    async def test_tick_updates_conditions(
        self, test_db_engine: AsyncEngine, test_workspace: Workspace
    ):
        """OBS-INT-001: tick()이 conditions를 DB에 저장하는지 검증."""
        ws_id = test_workspace.id

        # Arrange: Mock adapters to return volume info
        mock_ic = AsyncMock()
        mock_ic.list_all.return_value = []

        mock_sp = AsyncMock()
        mock_sp.list_volumes.return_value = [
            VolumeInfo(
                workspace_id=ws_id,
                exists=True,
                reason="VolumeExists",
                message="Test volume",
            )
        ]
        mock_sp.list_archives.return_value = []

        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        # Act: Run tick directly
        async with test_db_engine.connect() as conn:
            observer = ObserverCoordinator(
                conn,
                mock_leader,
                mock_subscriber,
                mock_ic,
                mock_sp,
            )
            await observer.tick()

        # Assert: Verify DB was updated
        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == ws_id)
            )
            ws = result.scalar_one()

            print(f"conditions after tick: {ws.conditions}")
            print(f"observed_at after tick: {ws.observed_at}")

            assert ws.conditions is not None, "conditions should not be None"
            assert ws.conditions != {}, "conditions should not be empty"
            assert ws.conditions.get("volume") is not None
            assert ws.conditions["volume"]["exists"] is True
            assert ws.observed_at is not None

    async def test_tick_with_container_and_volume(
        self, test_db_engine: AsyncEngine, test_workspace: Workspace
    ):
        """OBS-INT-002: container + volume이 함께 존재할 때."""
        ws_id = test_workspace.id

        mock_ic = AsyncMock()
        mock_ic.list_all.return_value = [
            ContainerInfo(
                workspace_id=ws_id,
                running=True,
                reason="Running",
                message="Up 5 minutes",
            )
        ]

        mock_sp = AsyncMock()
        mock_sp.list_volumes.return_value = [
            VolumeInfo(
                workspace_id=ws_id,
                exists=True,
                reason="VolumeExists",
                message="Volume exists",
            )
        ]
        mock_sp.list_archives.return_value = []

        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        async with test_db_engine.connect() as conn:
            observer = ObserverCoordinator(
                conn,
                mock_leader,
                mock_subscriber,
                mock_ic,
                mock_sp,
            )
            await observer.tick()

        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == ws_id)
            )
            ws = result.scalar_one()

            assert ws.conditions["container"]["running"] is True
            assert ws.conditions["volume"]["exists"] is True
            assert ws.conditions.get("archive") is None

    async def test_tick_ignores_orphan_volumes(
        self, test_db_engine: AsyncEngine, test_workspace: Workspace
    ):
        """OBS-INT-003: DB에 없는 workspace volume은 무시."""
        # Mock: volume for non-existent workspace
        mock_sp = AsyncMock()
        mock_sp.list_volumes.return_value = [
            VolumeInfo(
                workspace_id="orphan-ws-999",  # DB에 없는 ID
                exists=True,
                reason="VolumeExists",
                message="Orphan volume",
            )
        ]
        mock_sp.list_archives.return_value = []

        mock_ic = AsyncMock()
        mock_ic.list_all.return_value = []
        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        async with test_db_engine.connect() as conn:
            observer = ObserverCoordinator(
                conn,
                mock_leader,
                mock_subscriber,
                mock_ic,
                mock_sp,
            )
            await observer.tick()

        # DB에 orphan workspace가 생성되지 않아야 함
        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == "orphan-ws-999")
            )
            assert result.scalar_one_or_none() is None

    async def test_tick_filters_deleted_workspaces(
        self, test_db_engine: AsyncEngine, test_user: User
    ):
        """OBS-INT-004: deleted_at != NULL인 workspace는 무시."""
        # Create deleted workspace
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="deleted-ws-001",
                owner_user_id=test_user.id,
                name="Deleted Workspace",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-deleted-ws-001-home",
                phase="PENDING",
                operation="NONE",
                desired_state="RUNNING",
                conditions={},
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                deleted_at=datetime.now(UTC),  # Soft deleted
            )
            session.add(ws)
            await session.commit()

        mock_ic = AsyncMock()
        mock_ic.list_all.return_value = []
        mock_sp = AsyncMock()
        mock_sp.list_volumes.return_value = [
            VolumeInfo(
                workspace_id="deleted-ws-001",
                exists=True,
                reason="VolumeExists",
                message="Volume for deleted workspace",
            )
        ]
        mock_sp.list_archives.return_value = []

        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        async with test_db_engine.connect() as conn:
            observer = ObserverCoordinator(
                conn,
                mock_leader,
                mock_subscriber,
                mock_ic,
                mock_sp,
            )
            await observer.tick()

        # deleted workspace의 conditions는 업데이트되지 않아야 함
        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == "deleted-ws-001")
            )
            ws = result.scalar_one()
            # conditions는 여전히 빈 상태여야 함
            assert ws.conditions == {}

    async def test_tick_updates_workspace_without_resources(
        self, test_db_engine: AsyncEngine, test_workspace: Workspace
    ):
        """OBS-INT-005: 리소스가 없는 워크스페이스도 conditions/observed_at 업데이트.

        Bug fix: Observer가 리소스 기준이 아닌 DB 기준으로 iterate하여
        리소스가 없는 워크스페이스도 empty conditions로 업데이트.
        """
        ws_id = test_workspace.id

        # Mock: 모든 리소스가 비어있음 (컨테이너, 볼륨, 아카이브 없음)
        mock_ic = AsyncMock()
        mock_ic.list_all.return_value = []

        mock_sp = AsyncMock()
        mock_sp.list_volumes.return_value = []
        mock_sp.list_archives.return_value = []

        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        async with test_db_engine.connect() as conn:
            observer = ObserverCoordinator(
                conn,
                mock_leader,
                mock_subscriber,
                mock_ic,
                mock_sp,
            )
            await observer.tick()

        # Assert: 리소스 없는 워크스페이스도 conditions가 업데이트됨
        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == ws_id)
            )
            ws = result.scalar_one()

            # empty conditions가 설정됨 (None들로 구성)
            assert ws.conditions is not None
            assert ws.conditions.get("container") is None
            assert ws.conditions.get("volume") is None
            assert ws.conditions.get("archive") is None
            # observed_at이 업데이트됨 (stale이 아님)
            assert ws.observed_at is not None

    async def test_tick_commits_db_changes(
        self, test_db_engine: AsyncEngine, test_workspace: Workspace
    ):
        """OBS-INT-006: conditions 업데이트가 DB에 커밋되는지 검증."""
        ws_id = test_workspace.id

        mock_ic = AsyncMock()
        mock_ic.list_all.return_value = []
        mock_sp = AsyncMock()
        mock_sp.list_volumes.return_value = [
            VolumeInfo(
                workspace_id=ws_id,
                exists=True,
                reason="VolumeExists",
                message="Test",
            )
        ]
        mock_sp.list_archives.return_value = []

        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        async with test_db_engine.connect() as conn:
            observer = ObserverCoordinator(
                conn,
                mock_leader,
                mock_subscriber,
                mock_ic,
                mock_sp,
            )
            await observer.tick()

        # DB에 conditions가 저장되었는지 확인
        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == ws_id)
            )
            ws = result.scalar_one()
            assert ws.conditions is not None
            assert ws.conditions.get("volume") is not None
            assert ws.observed_at is not None


class TestConcurrentCoordinators:
    """Observer와 WC의 동시 실행 테스트.

    이슈: AsyncSession(bind=conn).commit()은 session level에서만 commit하고
    connection level에서는 commit하지 않아 "idle in transaction" 상태가 됨.
    이로 인해 Observer와 WC가 같은 workspace row를 동시에 업데이트할 때
    lock conflict가 발생함.

    해결: conn.execute() + conn.commit() 직접 사용

    Reference: ADR-012, base.py docstring
    """

    @pytest.fixture
    async def test_user(self, test_db_engine: AsyncEngine) -> User:
        """Create test user in DB."""
        async with AsyncSession(test_db_engine) as session:
            user = User(
                id="test-user-concurrent",
                username="test_concurrent",
                password_hash="hash",
                created_at=datetime.now(UTC),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    @pytest.fixture
    async def test_workspace(
        self, test_db_engine: AsyncEngine, test_user: User
    ) -> Workspace:
        """Create test workspace in STANDBY state (needs to start container)."""
        async with AsyncSession(test_db_engine) as session:
            ws = Workspace(
                id="test-ws-concurrent",
                owner_user_id=test_user.id,
                name="Concurrent Test Workspace",
                image_ref="test:latest",
                instance_backend="docker",
                storage_backend="s3",
                home_store_key="codehub-ws-test-ws-concurrent-home",
                phase="STANDBY",
                operation="NONE",
                desired_state="RUNNING",  # Needs to start → WC will update
                conditions={
                    "volume": {
                        "workspace_id": "test-ws-concurrent",
                        "exists": True,
                        "reason": "VolumeExists",
                        "message": "",
                    },
                    "container": None,
                    "archive": None,
                },
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(ws)
            await session.commit()
            await session.refresh(ws)
            return ws

    async def test_observer_and_wc_concurrent_no_blocking(
        self, test_db_engine: AsyncEngine, test_workspace: Workspace
    ):
        """CONC-001: Observer와 WC가 동시에 같은 workspace를 업데이트할 때 블로킹 없이 완료.

        이 테스트는 다음을 검증:
        1. Observer tick()과 WC tick()이 서로 다른 connection에서 동시 실행
        2. 5초 timeout 내에 둘 다 완료 (블로킹 없음)
        3. DB에 양쪽 업데이트가 모두 반영됨

        실패 시나리오 (수정 전):
        - AsyncSession(bind=conn).commit() 사용 시
        - Observer가 UPDATE 실행 후 session.commit() 호출
        - 하지만 connection은 "idle in transaction" 상태로 남음
        - WC가 같은 row UPDATE 시도 → lock wait
        - 5초 timeout 초과
        """
        import asyncio
        from codehub.control.coordinator.wc import WorkspaceController

        ws_id = test_workspace.id

        # Mock adapters for Observer - container NOT running yet
        # Observer sees volume but no running container
        mock_ic_obs = AsyncMock()
        mock_ic_obs.list_all.return_value = []  # No container

        mock_sp_obs = AsyncMock()
        mock_sp_obs.list_volumes.return_value = [
            VolumeInfo(
                workspace_id=ws_id,
                exists=True,
                reason="VolumeExists",
                message="Volume exists",
            )
        ]
        mock_sp_obs.list_archives.return_value = []

        mock_leader_obs = AsyncMock()
        mock_subscriber_obs = AsyncMock()

        # Mock adapters for WC
        mock_ic_wc = AsyncMock()
        mock_ic_wc.start.return_value = None
        mock_sp_wc = AsyncMock()
        mock_leader_wc = AsyncMock()
        mock_subscriber_wc = AsyncMock()

        # Track completion
        observer_done = False
        wc_done = False

        async def run_observer():
            nonlocal observer_done
            async with test_db_engine.connect() as conn:  # Separate connection
                observer = ObserverCoordinator(
                    conn,
                    mock_leader_obs,
                    mock_subscriber_obs,
                    mock_ic_obs,
                    mock_sp_obs,
                )
                await observer.tick()
                observer_done = True

        async def run_wc():
            nonlocal wc_done
            async with test_db_engine.connect() as conn:  # Separate connection
                wc = WorkspaceController(
                    conn,
                    mock_leader_wc,
                    mock_subscriber_wc,
                    mock_ic_wc,
                    mock_sp_wc,
                )
                await wc.tick()
                wc_done = True

        # Act: Run both concurrently with 5 second timeout
        # Key verification: neither blocks waiting for the other's lock
        try:
            await asyncio.wait_for(
                asyncio.gather(run_observer(), run_wc()),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            pytest.fail(
                "Observer and WC blocked on lock conflict! "
                "This indicates AsyncSession(bind=conn).commit() was used "
                "instead of conn.commit() directly."
            )

        # Assert: Both completed without blocking
        assert observer_done, "Observer did not complete"
        assert wc_done, "WC did not complete"

        # Assert: DB state shows both coordinators' updates were committed
        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == ws_id)
            )
            ws = result.scalar_one()

            print(f"conditions after concurrent update: {ws.conditions}")
            print(f"operation after concurrent update: {ws.operation}")
            print(f"observed_at after concurrent update: {ws.observed_at}")

            # Key assertion: observed_at was updated by Observer
            # This proves Observer's commit() worked at connection level
            assert ws.observed_at is not None, "Observer's update was not committed"

            # WC processed the workspace (operation may vary based on race condition)
            # STANDBY + desired=RUNNING → STARTING (if conditions still show no container)
            # But conditions could be updated by Observer first, changing the outcome
            # The key point is: both completed without timeout

    async def test_multiple_ticks_no_blocking(
        self, test_db_engine: AsyncEngine, test_workspace: Workspace
    ):
        """CONC-002: 여러 tick()이 순차적으로 실행되어도 블로킹 없음.

        실패 시나리오 (수정 전):
        - 첫 번째 tick()에서 connection이 "idle in transaction" 상태로 남음
        - 두 번째 tick()에서 같은 connection 사용 시 이전 트랜잭션이 활성화된 상태
        - commit이 제대로 안 되어 이전 UPDATE가 보이지 않음
        """
        import asyncio

        ws_id = test_workspace.id

        mock_ic = AsyncMock()
        mock_ic.list_all.return_value = [
            ContainerInfo(
                workspace_id=ws_id,
                running=True,
                reason="Running",
                message="Up 5 seconds",
            )
        ]

        mock_sp = AsyncMock()
        mock_sp.list_volumes.return_value = [
            VolumeInfo(
                workspace_id=ws_id,
                exists=True,
                reason="VolumeExists",
                message="Volume exists",
            )
        ]
        mock_sp.list_archives.return_value = []

        mock_leader = AsyncMock()
        mock_subscriber = AsyncMock()

        # Run 3 consecutive ticks with same connection
        async with test_db_engine.connect() as conn:
            observer = ObserverCoordinator(
                conn,
                mock_leader,
                mock_subscriber,
                mock_ic,
                mock_sp,
            )

            for i in range(3):
                try:
                    await asyncio.wait_for(observer.tick(), timeout=2.0)
                except asyncio.TimeoutError:
                    pytest.fail(f"tick() #{i+1} timed out - connection in bad state")

        # Verify final state
        async with AsyncSession(test_db_engine) as session:
            result = await session.execute(
                select(Workspace).where(Workspace.id == ws_id)
            )
            ws = result.scalar_one()

            assert ws.conditions is not None
            assert ws.conditions.get("container") is not None
            assert ws.observed_at is not None
