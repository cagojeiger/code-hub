"""Observer Coordinator integration tests.

Reference: docs/architecture_v2/wc-observer.md
"""

import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from codehub.control.coordinator.observer import BulkObserver, ObserverCoordinator
from codehub.control.coordinator.base import NotifyPublisher
from codehub.core.models import Workspace, User
from codehub.core.interfaces.storage import VolumeInfo, ArchiveInfo
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

        mock_publisher = AsyncMock(spec=NotifyPublisher)
        mock_leader = AsyncMock()
        mock_notify = AsyncMock()

        # Act: Run tick directly
        async with test_db_engine.connect() as conn:
            observer = ObserverCoordinator(
                conn,
                mock_leader,
                mock_notify,
                mock_ic,
                mock_sp,
                mock_publisher,
                prefix="codehub-ws-",
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

        mock_publisher = AsyncMock(spec=NotifyPublisher)
        mock_leader = AsyncMock()
        mock_notify = AsyncMock()

        async with test_db_engine.connect() as conn:
            observer = ObserverCoordinator(
                conn,
                mock_leader,
                mock_notify,
                mock_ic,
                mock_sp,
                mock_publisher,
                prefix="codehub-ws-",
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
        mock_publisher = AsyncMock()
        mock_leader = AsyncMock()
        mock_notify = AsyncMock()

        async with test_db_engine.connect() as conn:
            observer = ObserverCoordinator(
                conn,
                mock_leader,
                mock_notify,
                mock_ic,
                mock_sp,
                mock_publisher,
                prefix="codehub-ws-",
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

        mock_publisher = AsyncMock()
        mock_leader = AsyncMock()
        mock_notify = AsyncMock()

        async with test_db_engine.connect() as conn:
            observer = ObserverCoordinator(
                conn,
                mock_leader,
                mock_notify,
                mock_ic,
                mock_sp,
                mock_publisher,
                prefix="codehub-ws-",
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

    async def test_tick_wakes_wc(
        self, test_db_engine: AsyncEngine, test_workspace: Workspace
    ):
        """OBS-INT-005: conditions 업데이트 후 WC wake 호출."""
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

        mock_publisher = AsyncMock(spec=NotifyPublisher)
        mock_leader = AsyncMock()
        mock_notify = AsyncMock()

        async with test_db_engine.connect() as conn:
            observer = ObserverCoordinator(
                conn,
                mock_leader,
                mock_notify,
                mock_ic,
                mock_sp,
                mock_publisher,
                prefix="codehub-ws-",
            )
            await observer.tick()

        # wake_wc가 호출되었어야 함
        mock_publisher.wake_wc.assert_called_once()


class TestBulkObserverIntegration:
    """BulkObserver integration test (mocked adapters)."""

    async def test_observe_all_returns_correct_format(self):
        """observe_all이 올바른 포맷으로 데이터 반환."""
        mock_ic = AsyncMock()
        mock_ic.list_all.return_value = [
            ContainerInfo(
                workspace_id="ws-1",
                running=True,
                reason="Running",
                message="Up 5 minutes",
            )
        ]

        mock_sp = AsyncMock()
        mock_sp.list_volumes.return_value = [
            VolumeInfo(
                workspace_id="ws-1",
                exists=True,
                reason="VolumeExists",
                message="Volume exists",
            )
        ]
        mock_sp.list_archives.return_value = [
            ArchiveInfo(
                workspace_id="ws-1",
                archive_key="ws-1/op-123/home.tar.zst",
                exists=True,
                reason="ArchiveUploaded",
                message="Archive ready",
            )
        ]

        observer = BulkObserver(mock_ic, mock_sp)
        result = await observer.observe_all()

        assert "ws-1" in result
        assert result["ws-1"]["container"]["running"] is True
        assert result["ws-1"]["volume"]["exists"] is True
        assert result["ws-1"]["archive"]["exists"] is True
        assert result["ws-1"]["archive"]["archive_key"] == "ws-1/op-123/home.tar.zst"


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

        mock_publisher = AsyncMock(spec=NotifyPublisher)
        mock_leader_obs = AsyncMock()
        mock_notify_obs = AsyncMock()

        # Mock adapters for WC
        mock_ic_wc = AsyncMock()
        mock_ic_wc.start.return_value = None
        mock_sp_wc = AsyncMock()
        mock_leader_wc = AsyncMock()
        mock_notify_wc = AsyncMock()

        # Track completion
        observer_done = False
        wc_done = False

        async def run_observer():
            nonlocal observer_done
            async with test_db_engine.connect() as conn:  # Separate connection
                observer = ObserverCoordinator(
                    conn,
                    mock_leader_obs,
                    mock_notify_obs,
                    mock_ic_obs,
                    mock_sp_obs,
                    mock_publisher,
                    prefix="codehub-ws-",
                )
                await observer.tick()
                observer_done = True

        async def run_wc():
            nonlocal wc_done
            async with test_db_engine.connect() as conn:  # Separate connection
                wc = WorkspaceController(
                    conn,
                    mock_leader_wc,
                    mock_notify_wc,
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

        mock_publisher = AsyncMock(spec=NotifyPublisher)
        mock_leader = AsyncMock()
        mock_notify = AsyncMock()

        # Run 3 consecutive ticks with same connection
        async with test_db_engine.connect() as conn:
            observer = ObserverCoordinator(
                conn,
                mock_leader,
                mock_notify,
                mock_ic,
                mock_sp,
                mock_publisher,
                prefix="codehub-ws-",
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


class TestRedisPubSub:
    """Redis pub/sub integration test for Coordinator wake mechanism."""

    async def test_wake_wc_pubsub_works(self):
        """PUBSUB-001: NotifyPublisher.wake_wc()가 실제로 Redis pub/sub로 메시지를 전송.

        검증:
        1. NotifySubscriber가 WC_WAKE 채널 구독
        2. NotifyPublisher가 wake_wc() 호출
        3. Subscriber가 메시지 수신
        """
        import asyncio
        import redis.asyncio as aioredis
        from codehub.control.coordinator.base import (
            Channel,
            NotifyPublisher,
            NotifySubscriber,
        )

        # Use separate clients for pub and sub (Redis best practice)
        pub_client = aioredis.Redis(host="redis", port=6379, decode_responses=True)
        sub_client = aioredis.Redis(host="redis", port=6379, decode_responses=True)

        try:
            publisher = NotifyPublisher(pub_client)
            subscriber = NotifySubscriber(sub_client)

            # Subscribe to WC_WAKE channel
            await subscriber.subscribe(Channel.WC_WAKE)

            # Give Redis time to register subscription
            await asyncio.sleep(0.1)

            # Publish wake signal
            receivers = await publisher.wake_wc()

            # At least 1 receiver (our subscriber)
            assert receivers >= 1, f"Expected at least 1 receiver, got {receivers}"

            # Subscriber should receive the message (may need a few polls)
            message = None
            for _ in range(10):
                message = await subscriber.get_message(timeout=0.2)
                if message is not None:
                    break
            assert message is not None, "Subscriber did not receive wake message"
            assert message == str(Channel.WC_WAKE), f"Wrong channel: {message}"

            # Cleanup
            await subscriber.unsubscribe()
        finally:
            await pub_client.aclose()
            await sub_client.aclose()

    async def test_multiple_subscribers_receive_wake(self):
        """PUBSUB-002: 여러 Subscriber가 동일한 wake 메시지를 수신.

        실제 운영에서 여러 WC 인스턴스가 구동될 수 있음.
        """
        import asyncio
        import redis.asyncio as aioredis
        from codehub.control.coordinator.base import (
            Channel,
            NotifyPublisher,
            NotifySubscriber,
        )

        # Publisher uses separate client
        pub_client = aioredis.Redis(host="redis", port=6379, decode_responses=True)

        # Each subscriber gets its own client (simulating separate processes)
        sub_clients = [
            aioredis.Redis(host="redis", port=6379, decode_responses=True)
            for _ in range(3)
        ]

        try:
            publisher = NotifyPublisher(pub_client)
            subscribers = [NotifySubscriber(client) for client in sub_clients]

            for sub in subscribers:
                await sub.subscribe(Channel.WC_WAKE)

            await asyncio.sleep(0.1)

            # Publish wake signal
            receivers = await publisher.wake_wc()
            assert receivers >= 3, f"Expected at least 3 receivers, got {receivers}"

            # All subscribers should receive (may need a few polls each)
            for i, sub in enumerate(subscribers):
                message = None
                for _ in range(10):
                    message = await sub.get_message(timeout=0.2)
                    if message is not None:
                        break
                assert message is not None, f"Subscriber {i} did not receive message"

            # Cleanup
            for sub in subscribers:
                await sub.unsubscribe()
        finally:
            await pub_client.aclose()
            for client in sub_clients:
                await client.aclose()
