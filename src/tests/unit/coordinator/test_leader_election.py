"""Unit tests for LeaderElection class."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub.infra.pg_leader import SQLAlchemyLeaderElection, _compute_lock_id


class TestComputeLockId:
    """_compute_lock_id() 함수 테스트."""

    def test_lock_id_is_63bit_positive(self) -> None:
        """Lock ID는 63-bit 양수 범위 내."""
        lock_id = _compute_lock_id("test_key")

        # 0 <= lock_id < 2^63
        assert lock_id >= 0
        assert lock_id < (1 << 63)

    def test_lock_id_deterministic(self) -> None:
        """같은 key는 같은 lock_id."""
        lock_id_1 = _compute_lock_id("coordinator:wc")
        lock_id_2 = _compute_lock_id("coordinator:wc")

        assert lock_id_1 == lock_id_2

    def test_different_keys_different_ids(self) -> None:
        """다른 key는 다른 lock_id."""
        lock_id_wc = _compute_lock_id("coordinator:wc")
        lock_id_gc = _compute_lock_id("coordinator:gc")

        assert lock_id_wc != lock_id_gc

    def test_no_collision_for_coordinator_types(self) -> None:
        """모든 CoordinatorType에 대해 lock_id 충돌 없음."""
        keys = ["coordinator:wc", "coordinator:gc", "coordinator:ttl", "coordinator:observer"]
        lock_ids = [_compute_lock_id(k) for k in keys]

        # 모두 고유
        assert len(set(lock_ids)) == len(keys)


class TestLeaderElectionInit:
    """LeaderElection 초기화 테스트."""

    def test_init_sets_lock_id(self) -> None:
        """초기화 시 lock_id 계산."""
        conn = AsyncMock()
        leader = SQLAlchemyLeaderElection(conn, "test_lock")

        assert leader.lock_id == _compute_lock_id("test_lock")
        assert leader.is_leader is False

    def test_init_converts_enum_to_str(self) -> None:
        """Enum도 str로 변환."""
        from codehub.control.coordinator.base import CoordinatorType

        conn = AsyncMock()
        leader = SQLAlchemyLeaderElection(conn, CoordinatorType.WC)

        assert leader._lock_key == "wc"


class TestTryAcquire:
    """try_acquire() 테스트."""

    @pytest.mark.asyncio
    async def test_skips_db_call_when_already_leader(self) -> None:
        """P0: 이미 리더면 DB 호출 스킵."""
        conn = AsyncMock()
        leader = SQLAlchemyLeaderElection(conn, "test_lock")

        # 이미 리더 상태 설정
        leader._is_leader = True

        result = await leader.try_acquire()

        assert result is True
        conn.execute.assert_not_called()  # DB 호출 없음

    @pytest.mark.asyncio
    async def test_acquires_lock_on_first_call(self) -> None:
        """첫 호출 시 락 획득 시도."""
        conn = AsyncMock()
        result_mock = MagicMock()
        result_mock.fetchone.return_value = (True,)
        conn.execute = AsyncMock(return_value=result_mock)

        leader = SQLAlchemyLeaderElection(conn, "test_lock")
        result = await leader.try_acquire()

        assert result is True
        assert leader.is_leader is True
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_lock_not_acquired(self) -> None:
        """락 획득 실패 시 False 반환."""
        conn = AsyncMock()
        result_mock = MagicMock()
        result_mock.fetchone.return_value = (False,)
        conn.execute = AsyncMock(return_value=result_mock)

        leader = SQLAlchemyLeaderElection(conn, "test_lock")
        result = await leader.try_acquire()

        assert result is False
        assert leader.is_leader is False

    @pytest.mark.asyncio
    async def test_uses_parameter_binding(self) -> None:
        """P1: SQL 파라미터 바인딩 사용 (인젝션 방지)."""
        conn = AsyncMock()
        result_mock = MagicMock()
        result_mock.fetchone.return_value = (True,)
        conn.execute = AsyncMock(return_value=result_mock)

        leader = SQLAlchemyLeaderElection(conn, "test_lock")
        await leader.try_acquire()

        # execute 호출 시 dict 파라미터 사용 (SQLAlchemy path)
        call_args = conn.execute.call_args
        assert call_args is not None
        # 두 번째 인자가 dict (파라미터 바인딩)
        assert "lock_id" in call_args[0][1] or len(call_args[0]) == 2

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self) -> None:
        """P3: 타임아웃 시 False 반환."""
        conn = AsyncMock()

        async def slow_execute(*args, **kwargs):
            await asyncio.sleep(10)  # 매우 긴 대기
            return MagicMock(fetchone=lambda: (True,))

        conn.execute = slow_execute

        leader = SQLAlchemyLeaderElection(conn, "test_lock")
        result = await leader.try_acquire(timeout=0.01)  # 10ms 타임아웃

        assert result is False
        assert leader.is_leader is False


class TestRelease:
    """release() 테스트."""

    @pytest.mark.asyncio
    async def test_release_when_not_leader(self) -> None:
        """리더가 아니면 아무것도 안 함."""
        conn = AsyncMock()
        leader = SQLAlchemyLeaderElection(conn, "test_lock")

        # is_leader = False (기본값)
        await leader.release()

        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_release_when_leader(self) -> None:
        """리더일 때 락 해제."""
        conn = AsyncMock()
        result_mock = MagicMock()
        result_mock.fetchone.return_value = (True,)
        conn.execute = AsyncMock(return_value=result_mock)

        leader = SQLAlchemyLeaderElection(conn, "test_lock")
        leader._is_leader = True

        await leader.release()

        assert leader.is_leader is False
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_release_logs_warning_when_lock_not_held(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """락을 보유하지 않았을 때 WARNING 로그."""
        import logging

        caplog.set_level(logging.WARNING)

        conn = AsyncMock()
        result_mock = MagicMock()
        result_mock.fetchone.return_value = (False,)  # Lock was not held
        conn.execute = AsyncMock(return_value=result_mock)

        leader = SQLAlchemyLeaderElection(conn, "test_lock")
        leader._is_leader = True

        await leader.release()

        assert leader.is_leader is False
        assert "Lock was not held during release" in caplog.text


class TestVerifyHolding:
    """verify_holding() 테스트."""

    @pytest.mark.asyncio
    async def test_verify_returns_false_when_not_leader(self) -> None:
        """리더가 아니면 False 반환."""
        conn = AsyncMock()
        leader = SQLAlchemyLeaderElection(conn, "test_lock")

        result = await leader.verify_holding()

        assert result is False
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_returns_true_when_holding(self) -> None:
        """락 보유 중이면 True 반환."""
        conn = AsyncMock()
        result_mock = MagicMock()
        result_mock.fetchone.return_value = (True,)
        conn.execute = AsyncMock(return_value=result_mock)

        leader = SQLAlchemyLeaderElection(conn, "test_lock")
        leader._is_leader = True

        result = await leader.verify_holding()

        assert result is True
        assert leader.is_leader is True  # 상태 유지

    @pytest.mark.asyncio
    async def test_verify_detects_lost_lock(self) -> None:
        """P6: 락 잃음 감지."""
        conn = AsyncMock()
        result_mock = MagicMock()
        result_mock.fetchone.return_value = (False,)  # 락 없음
        conn.execute = AsyncMock(return_value=result_mock)

        leader = SQLAlchemyLeaderElection(conn, "test_lock")
        leader._is_leader = True
        leader._was_leader = True

        result = await leader.verify_holding()

        assert result is False
        assert leader.is_leader is False
        assert leader._was_leader is False

    @pytest.mark.asyncio
    async def test_verify_timeout_clears_leadership(self) -> None:
        """verify 타임아웃 시 리더십 포기."""
        conn = AsyncMock()

        async def slow_execute(*args, **kwargs):
            await asyncio.sleep(10)
            return MagicMock(fetchone=lambda: (True,))

        conn.execute = slow_execute

        leader = SQLAlchemyLeaderElection(conn, "test_lock")
        leader._is_leader = True

        result = await leader.verify_holding(timeout=0.01)

        assert result is False
        assert leader.is_leader is False


class TestStateLogging:
    """상태 변경 로깅 테스트."""

    @pytest.mark.asyncio
    async def test_logs_on_acquire(self, caplog: pytest.LogCaptureFixture) -> None:
        """리더십 획득 시 INFO 로그."""
        import logging

        caplog.set_level(logging.INFO)

        conn = AsyncMock()
        result_mock = MagicMock()
        result_mock.fetchone.return_value = (True,)
        conn.execute = AsyncMock(return_value=result_mock)

        leader = SQLAlchemyLeaderElection(conn, "test_lock")
        await leader.try_acquire()

        assert "Acquired leadership" in caplog.text

    @pytest.mark.asyncio
    async def test_logs_on_lost(self, caplog: pytest.LogCaptureFixture) -> None:
        """리더십 잃음 시 WARNING 로그."""
        import logging

        caplog.set_level(logging.WARNING)

        conn = AsyncMock()
        result_mock = MagicMock()
        # 첫 번째: True (획득), 두 번째: False (잃음)
        result_mock.fetchone.side_effect = [(True,), (False,)]
        conn.execute = AsyncMock(return_value=result_mock)

        leader = SQLAlchemyLeaderElection(conn, "test_lock")

        # 획득
        await leader.try_acquire()
        # 잃음 시뮬레이션: _is_leader 리셋 후 다시 시도
        leader._is_leader = False

        await leader.try_acquire()

        assert "Lost leadership" in caplog.text


class TestJitteredVerifyInterval:
    """_jittered_verify_interval() 테스트 (CoordinatorBase)."""

    def test_jitter_within_range(
        self, mock_conn: AsyncMock, mock_leader: AsyncMock, mock_subscriber: AsyncMock
    ) -> None:
        """Jitter가 ±30% 범위 내."""
        from codehub.control.coordinator.base import CoordinatorBase, CoordinatorType

        class TestCoordinator(CoordinatorBase):
            COORDINATOR_TYPE = CoordinatorType.WC
            WAKE_TARGET = "wc"

            async def reconcile(self) -> None:
                pass

        coord = TestCoordinator(mock_conn, mock_leader, mock_subscriber)

        # 100회 반복하여 모두 범위 내인지 확인
        min_expected = coord.VERIFY_INTERVAL * (1.0 - coord.VERIFY_JITTER)
        max_expected = coord.VERIFY_INTERVAL * (1.0 + coord.VERIFY_JITTER)

        for _ in range(100):
            result = coord._jittered_verify_interval()
            assert min_expected <= result <= max_expected
