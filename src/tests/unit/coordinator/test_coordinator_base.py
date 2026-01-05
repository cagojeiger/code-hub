"""Unit tests for CoordinatorBase."""

import time
from unittest.mock import AsyncMock

import pytest

from codehub.control.coordinator.base import (
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
)
from codehub.infra.redis_pubsub import NotifySubscriber, WakeTarget


class DummyCoordinator(CoordinatorBase):
    """Test용 더미 Coordinator."""

    COORDINATOR_TYPE = CoordinatorType.WC
    WAKE_TARGET = WakeTarget.WC

    # 빠른 테스트를 위해 간격 축소
    IDLE_INTERVAL = 0.5
    ACTIVE_INTERVAL = 0.1
    MIN_INTERVAL = 0.05
    ACTIVE_DURATION = 1.0

    def __init__(
        self,
        conn: AsyncMock,
        leader: LeaderElection | AsyncMock,
        notify: NotifySubscriber | AsyncMock,
    ) -> None:
        super().__init__(conn, leader, notify)
        self.tick_count = 0

    async def tick(self) -> None:
        self.tick_count += 1


class TestAccelerateAndIsActive:
    """accelerate()와 is_active 테스트."""

    def test_accelerate_extends_active_duration(
        self, mock_conn: AsyncMock, mock_leader: AsyncMock, mock_notify: AsyncMock
    ) -> None:
        """accelerate() 호출 시 ACTIVE_DURATION 연장."""
        coord = DummyCoordinator(mock_conn, mock_leader, mock_notify)

        # 만료 상태로 설정
        coord._active_until = time.time() - 1
        assert coord.is_active is False

        # accelerate 호출
        coord.accelerate()

        # 다시 active
        assert coord.is_active is True

    def test_is_active_returns_false_after_duration(
        self, mock_conn: AsyncMock, mock_leader: AsyncMock, mock_notify: AsyncMock
    ) -> None:
        """ACTIVE_DURATION 후 is_active=False."""
        coord = DummyCoordinator(mock_conn, mock_leader, mock_notify)

        # 만료 상태로 설정
        coord._active_until = time.time() - 1

        assert coord.is_active is False

    def test_is_active_returns_true_when_not_expired(
        self, mock_conn: AsyncMock, mock_leader: AsyncMock, mock_notify: AsyncMock
    ) -> None:
        """ACTIVE_DURATION 내에는 is_active=True."""
        coord = DummyCoordinator(mock_conn, mock_leader, mock_notify)

        # 생성 직후는 active
        assert coord.is_active is True


class TestGetInterval:
    """_get_interval() 테스트."""

    def test_get_interval_returns_active_when_active(
        self, mock_conn: AsyncMock, mock_leader: AsyncMock, mock_notify: AsyncMock
    ) -> None:
        """Active 상태 → ACTIVE_INTERVAL 반환."""
        coord = DummyCoordinator(mock_conn, mock_leader, mock_notify)

        coord.accelerate()

        assert coord._get_interval() == coord.ACTIVE_INTERVAL

    def test_get_interval_returns_idle_when_inactive(
        self, mock_conn: AsyncMock, mock_leader: AsyncMock, mock_notify: AsyncMock
    ) -> None:
        """Inactive 상태 → IDLE_INTERVAL 반환."""
        coord = DummyCoordinator(mock_conn, mock_leader, mock_notify)

        # 만료 상태로 설정
        coord._active_until = time.time() - 1

        assert coord._get_interval() == coord.IDLE_INTERVAL


class TestWaitForNotify:
    """_wait_for_notify() 테스트."""

    @pytest.mark.asyncio
    async def test_wait_for_notify_accelerates_on_message(
        self, mock_conn: AsyncMock, mock_leader: AsyncMock, mock_notify: AsyncMock
    ) -> None:
        """Redis 메시지 수신 시 accelerate() 호출."""
        coord = DummyCoordinator(mock_conn, mock_leader, mock_notify)

        # 만료 상태로 설정
        coord._active_until = time.time() - 1
        assert coord.is_active is False

        # 메시지 수신 시뮬레이션
        mock_notify.get_message = AsyncMock(return_value="wc:wake")

        await coord._wait_for_notify(10.0)

        # accelerate 되어 active 상태가 됨
        assert coord.is_active is True

    @pytest.mark.asyncio
    async def test_wait_for_notify_no_accelerate_without_message(
        self, mock_conn: AsyncMock, mock_leader: AsyncMock, mock_notify: AsyncMock
    ) -> None:
        """메시지 없으면 accelerate 안 함."""
        coord = DummyCoordinator(mock_conn, mock_leader, mock_notify)

        # 만료 상태로 설정
        coord._active_until = time.time() - 1
        assert coord.is_active is False

        # 메시지 없음
        mock_notify.get_message = AsyncMock(return_value=None)

        await coord._wait_for_notify(0.01)

        # 여전히 inactive
        assert coord.is_active is False


class TestThrottle:
    """_throttle() 테스트."""

    @pytest.mark.asyncio
    async def test_throttle_waits_min_interval(
        self, mock_conn: AsyncMock, mock_leader: AsyncMock, mock_notify: AsyncMock
    ) -> None:
        """MIN_INTERVAL 미만이면 대기."""
        coord = DummyCoordinator(mock_conn, mock_leader, mock_notify)

        # 방금 tick 실행한 것처럼 설정
        coord._last_tick = time.time()

        start = time.time()
        await coord._throttle()
        elapsed = time.time() - start

        # MIN_INTERVAL(0.05)만큼 대기
        assert elapsed >= coord.MIN_INTERVAL * 0.8  # 약간의 오차 허용

    @pytest.mark.asyncio
    async def test_throttle_no_wait_after_interval(
        self, mock_conn: AsyncMock, mock_leader: AsyncMock, mock_notify: AsyncMock
    ) -> None:
        """MIN_INTERVAL 이후면 대기 없음."""
        coord = DummyCoordinator(mock_conn, mock_leader, mock_notify)

        # MIN_INTERVAL 이전에 tick 실행한 것처럼 설정
        coord._last_tick = time.time() - coord.MIN_INTERVAL - 0.1

        start = time.time()
        await coord._throttle()
        elapsed = time.time() - start

        # 거의 즉시 반환
        assert elapsed < 0.01


class TestEnsureLeadership:
    """_ensure_leadership() 테스트."""

    @pytest.mark.asyncio
    async def test_ensure_leadership_acquires_on_first_call(
        self, mock_conn: AsyncMock, mock_leader: AsyncMock, mock_notify: AsyncMock
    ) -> None:
        """첫 호출 시 리더십 획득 시도."""
        coord = DummyCoordinator(mock_conn, mock_leader, mock_notify)

        result = await coord._ensure_leadership()

        assert result is True
        mock_leader.try_acquire.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_leadership_skips_if_recently_verified(
        self, mock_conn: AsyncMock, mock_leader: AsyncMock, mock_notify: AsyncMock
    ) -> None:
        """최근 검증했으면 스킵."""
        coord = DummyCoordinator(mock_conn, mock_leader, mock_notify)

        # 이미 리더이고 최근 검증됨
        coord._last_verify = time.time()

        result = await coord._ensure_leadership()

        assert result is True
        mock_leader.try_acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_leadership_returns_false_if_not_acquired(
        self, mock_conn: AsyncMock, mock_leader: AsyncMock, mock_notify: AsyncMock
    ) -> None:
        """리더십 획득 실패 시 False 반환."""
        mock_leader.try_acquire = AsyncMock(return_value=False)
        mock_leader.is_leader = False

        coord = DummyCoordinator(mock_conn, mock_leader, mock_notify)
        coord.LEADER_RETRY_INTERVAL = 0.01  # 빠른 테스트를 위해

        result = await coord._ensure_leadership()

        assert result is False
