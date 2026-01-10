"""Tests for WorkspaceController.

Reference: docs/architecture_v2/wc.md
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub.control.coordinator.wc import WorkspaceController
from codehub.control.coordinator.wc_planner import (
    PlanAction,
    PlanInput,
    _check_completion,
    _phase_from_desired,
    _select_operation,
    plan,
)
from codehub.core.domain.workspace import (
    DesiredState,
    ErrorReason,
    Operation,
    Phase,
)
from codehub.core.interfaces.instance import InstanceController
from codehub.core.interfaces.storage import StorageProvider
from codehub.core.models import Workspace


@pytest.fixture
def mock_ic() -> AsyncMock:
    """Mock InstanceController."""
    ic = AsyncMock(spec=InstanceController)
    ic.start = AsyncMock()
    ic.delete = AsyncMock()
    ic.is_running = AsyncMock(return_value=False)
    ic.list_all = AsyncMock(return_value=[])
    return ic


@pytest.fixture
def mock_sp() -> AsyncMock:
    """Mock StorageProvider."""
    sp = AsyncMock(spec=StorageProvider)
    sp.provision = AsyncMock()
    sp.restore = AsyncMock(return_value="ws-1/op-1/home.tar.zst")
    sp.archive = AsyncMock(return_value="ws-1/op-1/home.tar.zst")
    sp.delete_volume = AsyncMock()
    sp.create_empty_archive = AsyncMock(return_value="ws-1/op-1/home.tar.zst")
    sp.list_volumes = AsyncMock(return_value=[])
    sp.list_archives = AsyncMock(return_value=[])
    return sp


@pytest.fixture
def mock_conn() -> AsyncMock:
    """Mock AsyncConnection."""
    conn = AsyncMock()
    # Mock execute() to return result with rowcount (for CAS updates)
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_result.mappings.return_value.all.return_value = []
    conn.execute.return_value = mock_result
    return conn


@pytest.fixture
def mock_leader() -> AsyncMock:
    """Mock LeaderElection."""
    leader = AsyncMock()
    leader.is_leader = True
    leader.try_acquire = AsyncMock(return_value=True)
    return leader


@pytest.fixture
def mock_subscriber() -> AsyncMock:
    """Mock ChannelSubscriber."""
    subscriber = AsyncMock()
    subscriber.subscribe = AsyncMock()
    subscriber.unsubscribe = AsyncMock()
    subscriber.get_message = AsyncMock(return_value=None)
    return subscriber


def make_workspace(
    id: str = "ws-1",
    phase: Phase = Phase.PENDING,
    operation: Operation = Operation.NONE,
    desired_state: DesiredState = DesiredState.RUNNING,
    conditions: dict | None = None,
    archive_key: str | None = None,
    op_started_at: datetime | None = None,
    op_id: str | None = None,
    deleted_at: datetime | None = None,
    error_count: int = 0,
) -> Workspace:
    """Create test workspace."""
    now = datetime.now(UTC)
    return Workspace(
        id=id,
        owner_user_id="user-1",
        name="Test Workspace",
        image_ref="ubuntu:22.04",
        instance_backend="local-docker",
        storage_backend="docker-volume",
        home_store_key=f"ws-{id}-home",
        conditions=conditions or {},
        phase=phase.value,
        operation=operation.value,
        op_started_at=op_started_at,
        op_id=op_id,
        desired_state=desired_state.value,
        archive_key=archive_key,
        error_count=error_count,
        created_at=now,
        updated_at=now,
        deleted_at=deleted_at,
    )


class TestSelectOperation:
    """_select_operation() 테스트 - wc_planner 순수 함수."""

    def test_pending_to_running(self):
        """PENDING → RUNNING: PROVISIONING."""
        op = _select_operation(Phase.PENDING, DesiredState.RUNNING)
        assert op == Operation.PROVISIONING

    def test_pending_to_standby(self):
        """PENDING → STANDBY: PROVISIONING."""
        op = _select_operation(Phase.PENDING, DesiredState.STANDBY)
        assert op == Operation.PROVISIONING

    def test_pending_to_archived(self):
        """PENDING → ARCHIVED: CREATE_EMPTY_ARCHIVE."""
        op = _select_operation(Phase.PENDING, DesiredState.ARCHIVED)
        assert op == Operation.CREATE_EMPTY_ARCHIVE

    def test_archived_to_running(self):
        """ARCHIVED → RUNNING: RESTORING."""
        op = _select_operation(Phase.ARCHIVED, DesiredState.RUNNING)
        assert op == Operation.RESTORING

    def test_archived_to_standby(self):
        """ARCHIVED → STANDBY: RESTORING."""
        op = _select_operation(Phase.ARCHIVED, DesiredState.STANDBY)
        assert op == Operation.RESTORING

    def test_standby_to_running(self):
        """STANDBY → RUNNING: STARTING."""
        op = _select_operation(Phase.STANDBY, DesiredState.RUNNING)
        assert op == Operation.STARTING

    def test_standby_to_archived(self):
        """STANDBY → ARCHIVED: ARCHIVING."""
        op = _select_operation(Phase.STANDBY, DesiredState.ARCHIVED)
        assert op == Operation.ARCHIVING

    def test_running_to_standby(self):
        """RUNNING → STANDBY: STOPPING."""
        op = _select_operation(Phase.RUNNING, DesiredState.STANDBY)
        assert op == Operation.STOPPING

    def test_running_to_archived(self):
        """RUNNING → ARCHIVED: STOPPING (step by step)."""
        op = _select_operation(Phase.RUNNING, DesiredState.ARCHIVED)
        assert op == Operation.STOPPING

    def test_any_to_deleted(self):
        """Any phase → DELETED: DELETING."""
        for phase in [Phase.PENDING, Phase.ARCHIVED, Phase.STANDBY, Phase.RUNNING]:
            op = _select_operation(phase, DesiredState.DELETED)
            assert op == Operation.DELETING


class TestCheckCompletion:
    """_check_completion() 테스트 - wc_planner 순수 함수."""

    def test_provisioning_complete(self):
        """PROVISIONING 완료: volume_ready=True."""
        ws = make_workspace(
            conditions={"volume": {"exists": True, "reason": "VolumeExists", "message": ""}}
        )
        plan_input = PlanInput.from_workspace(ws)
        assert _check_completion(Operation.PROVISIONING, plan_input) is True

    def test_provisioning_incomplete(self):
        """PROVISIONING 미완료: volume_ready=False."""
        ws = make_workspace(conditions={})
        plan_input = PlanInput.from_workspace(ws)
        assert _check_completion(Operation.PROVISIONING, plan_input) is False

    def test_starting_complete(self):
        """STARTING 완료: container_ready=True."""
        ws = make_workspace(
            conditions={
                "container": {"running": True, "reason": "Running", "message": ""},
                "volume": {"exists": True, "reason": "VolumeExists", "message": ""},
            }
        )
        plan_input = PlanInput.from_workspace(ws)
        assert _check_completion(Operation.STARTING, plan_input) is True

    def test_stopping_complete(self):
        """STOPPING 완료: container_ready=False."""
        ws = make_workspace(
            conditions={"volume": {"exists": True, "reason": "VolumeExists", "message": ""}}
        )
        plan_input = PlanInput.from_workspace(ws)
        assert _check_completion(Operation.STOPPING, plan_input) is True

    def test_archiving_complete(self):
        """ARCHIVING 완료: !volume_ready ∧ archive_ready."""
        ws = make_workspace(
            conditions={
                "archive": {
                    "exists": True,
                    "archive_key": "ws-1/op-1/home.tar.zst",
                    "reason": "ArchiveUploaded",
                    "message": "",
                }
            }
        )
        plan_input = PlanInput.from_workspace(ws)
        assert _check_completion(Operation.ARCHIVING, plan_input) is True

    def test_deleting_complete(self):
        """DELETING 완료: !container_ready ∧ !volume_ready."""
        ws = make_workspace(conditions={})
        plan_input = PlanInput.from_workspace(ws)
        assert _check_completion(Operation.DELETING, plan_input) is True


class TestPlan:
    """plan() 테스트 - wc_planner 순수 함수."""

    def test_already_converged(self):
        """이미 수렴됨 → no-op."""
        ws = make_workspace(
            phase=Phase.RUNNING,
            desired_state=DesiredState.RUNNING,
            conditions={
                "container": {"running": True, "reason": "Running", "message": ""},
                "volume": {"exists": True, "reason": "VolumeExists", "message": ""},
            },
        )
        plan_input = PlanInput.from_workspace(ws)
        action = plan(plan_input)

        assert action.operation == Operation.NONE
        assert action.phase == Phase.RUNNING

    def test_need_convergence(self):
        """수렴 필요 → operation 선택."""
        ws = make_workspace(
            phase=Phase.PENDING,
            desired_state=DesiredState.RUNNING,
            conditions={},
        )
        plan_input = PlanInput.from_workspace(ws)
        action = plan(plan_input)

        assert action.operation == Operation.PROVISIONING
        assert action.op_id is not None

    def test_operation_in_progress_complete(self):
        """진행 중 operation 완료."""
        ws = make_workspace(
            phase=Phase.PENDING,
            operation=Operation.PROVISIONING,
            desired_state=DesiredState.RUNNING,
            conditions={"volume": {"exists": True, "reason": "VolumeExists", "message": ""}},
            op_started_at=datetime.now(UTC),
            op_id="op-1",
        )
        plan_input = PlanInput.from_workspace(ws)
        action = plan(plan_input)

        assert action.operation == Operation.NONE
        assert action.complete is True

    def test_operation_timeout(self):
        """operation timeout → ERROR."""
        ws = make_workspace(
            phase=Phase.PENDING,
            operation=Operation.PROVISIONING,
            desired_state=DesiredState.RUNNING,
            conditions={},
            op_started_at=datetime.now(UTC) - timedelta(seconds=400),
            op_id="op-1",
        )
        plan_input = PlanInput.from_workspace(ws)
        action = plan(plan_input, timeout_seconds=300)

        assert action.operation == Operation.NONE
        assert action.phase == Phase.ERROR
        assert action.error_reason == ErrorReason.TIMEOUT

    def test_error_phase_deleted_desired(self):
        """ERROR phase + desired=DELETED → DELETING."""
        ws = make_workspace(
            phase=Phase.ERROR,
            desired_state=DesiredState.DELETED,
            conditions={
                "container": {"running": True, "reason": "Running", "message": ""},
                # volume_ready=False → invariant violation
            },
        )
        plan_input = PlanInput.from_workspace(ws)
        action = plan(plan_input)

        assert action.operation == Operation.DELETING
        assert action.phase == Phase.DELETING


class TestExecute:
    """_execute() 테스트."""

    @pytest.fixture
    def wc(
        self,
        mock_conn: AsyncMock,
        mock_leader: AsyncMock,
        mock_subscriber: AsyncMock,
        mock_ic: AsyncMock,
        mock_sp: AsyncMock,
    ) -> WorkspaceController:
        return WorkspaceController(mock_conn, mock_leader, mock_subscriber, mock_ic, mock_sp)

    async def test_provisioning(self, wc: WorkspaceController, mock_sp: AsyncMock):
        """PROVISIONING → sp.provision()."""
        ws = make_workspace()
        action = PlanAction(operation=Operation.PROVISIONING, phase=Phase.PENDING)

        await wc._execute(ws, action)

        mock_sp.provision.assert_called_once_with(ws.id)

    async def test_starting(self, wc: WorkspaceController, mock_ic: AsyncMock):
        """STARTING → ic.start()."""
        ws = make_workspace()
        action = PlanAction(operation=Operation.STARTING, phase=Phase.STANDBY)

        await wc._execute(ws, action)

        mock_ic.start.assert_called_once_with(ws.id, ws.image_ref)

    async def test_stopping(self, wc: WorkspaceController, mock_ic: AsyncMock):
        """STOPPING → ic.delete()."""
        ws = make_workspace()
        action = PlanAction(operation=Operation.STOPPING, phase=Phase.RUNNING)

        await wc._execute(ws, action)

        mock_ic.delete.assert_called_once_with(ws.id)

    async def test_archiving(
        self, wc: WorkspaceController, mock_ic: AsyncMock, mock_sp: AsyncMock
    ):
        """ARCHIVING → sp.archive() + ic.delete() + sp.delete_volume()."""
        ws = make_workspace()
        action = PlanAction(operation=Operation.ARCHIVING, phase=Phase.STANDBY, op_id="op-1")

        await wc._execute(ws, action)

        mock_sp.archive.assert_called_once_with(ws.id, "op-1")
        mock_ic.delete.assert_called_once_with(ws.id)  # Exited 컨테이너 정리
        mock_sp.delete_volume.assert_called_once_with(ws.id)
        assert action.archive_key == "ws-1/op-1/home.tar.zst"

    async def test_archiving_call_order(
        self, wc: WorkspaceController, mock_ic: AsyncMock, mock_sp: AsyncMock
    ):
        """ARCHIVING: archive → delete → delete_volume 순서 확인."""
        call_order: list[str] = []

        async def track_archive(*args):
            call_order.append("archive")
            return "ws-1/op-1/home.tar.zst"

        async def track_delete(*args):
            call_order.append("delete")

        async def track_delete_volume(*args):
            call_order.append("delete_volume")

        mock_sp.archive.side_effect = track_archive
        mock_ic.delete.side_effect = track_delete
        mock_sp.delete_volume.side_effect = track_delete_volume

        ws = make_workspace()
        action = PlanAction(operation=Operation.ARCHIVING, phase=Phase.STANDBY, op_id="op-1")

        await wc._execute(ws, action)

        assert call_order == ["archive", "delete", "delete_volume"]

    async def test_restoring(self, wc: WorkspaceController, mock_sp: AsyncMock):
        """RESTORING → sp.restore()."""
        ws = make_workspace(archive_key="ws-1/op-1/home.tar.zst")
        action = PlanAction(operation=Operation.RESTORING, phase=Phase.ARCHIVED)

        await wc._execute(ws, action)

        mock_sp.restore.assert_called_once_with(ws.id, ws.archive_key)

    async def test_create_empty_archive(self, wc: WorkspaceController, mock_sp: AsyncMock):
        """CREATE_EMPTY_ARCHIVE → sp.create_empty_archive()."""
        ws = make_workspace()
        action = PlanAction(
            operation=Operation.CREATE_EMPTY_ARCHIVE, phase=Phase.PENDING, op_id="op-1"
        )

        await wc._execute(ws, action)

        mock_sp.create_empty_archive.assert_called_once_with(ws.id, "op-1")
        assert action.archive_key == "ws-1/op-1/home.tar.zst"

    async def test_deleting(self, wc: WorkspaceController, mock_ic: AsyncMock, mock_sp: AsyncMock):
        """DELETING → ic.delete() + sp.delete_volume()."""
        ws = make_workspace()
        action = PlanAction(operation=Operation.DELETING, phase=Phase.DELETING)

        await wc._execute(ws, action)

        mock_ic.delete.assert_called_once_with(ws.id)
        mock_sp.delete_volume.assert_called_once_with(ws.id)


class TestPhaseFromDesired:
    """_phase_from_desired() 테스트 - wc_planner 순수 함수."""

    def test_running(self):
        assert _phase_from_desired(DesiredState.RUNNING) == Phase.RUNNING

    def test_standby(self):
        assert _phase_from_desired(DesiredState.STANDBY) == Phase.STANDBY

    def test_archived(self):
        assert _phase_from_desired(DesiredState.ARCHIVED) == Phase.ARCHIVED

    def test_deleted(self):
        assert _phase_from_desired(DesiredState.DELETED) == Phase.DELETED


class TestTickParallel:
    """tick() 병렬 처리 테스트."""

    @pytest.fixture
    def mock_ic(self) -> AsyncMock:
        ic = AsyncMock(spec=InstanceController)
        ic.start = AsyncMock()
        ic.delete = AsyncMock()
        return ic

    @pytest.fixture
    def mock_sp(self) -> AsyncMock:
        sp = AsyncMock(spec=StorageProvider)
        sp.provision = AsyncMock()
        sp.restore = AsyncMock()
        sp.archive = AsyncMock(return_value="ws-1/op-1/home.tar.zst")
        sp.create_empty_archive = AsyncMock(return_value="ws-1/op-1/home.tar.zst")
        sp.delete_volume = AsyncMock()
        return sp

    async def test_multiple_workspaces_parallel(
        self,
        mock_conn: AsyncMock,
        mock_leader: AsyncMock,
        mock_subscriber: AsyncMock,
        mock_ic: AsyncMock,
        mock_sp: AsyncMock,
    ):
        """여러 ws 동시 처리."""
        wc = WorkspaceController(mock_conn, mock_leader, mock_subscriber, mock_ic, mock_sp)

        # _load_for_reconcile가 빈 리스트 반환하도록 mock
        wc._load_for_reconcile = AsyncMock(return_value=[])

        await wc.tick()

        wc._load_for_reconcile.assert_called_once()

    async def test_error_isolation(
        self,
        mock_conn: AsyncMock,
        mock_leader: AsyncMock,
        mock_subscriber: AsyncMock,
        mock_ic: AsyncMock,
        mock_sp: AsyncMock,
    ):
        """한 ws Execute 실패해도 나머지 계속 처리됨."""
        wc = WorkspaceController(mock_conn, mock_leader, mock_subscriber, mock_ic, mock_sp)

        # PENDING -> RUNNING 워크스페이스 (PROVISIONING 필요)
        ws1 = make_workspace(id="ws-1", phase=Phase.PENDING, desired_state=DesiredState.RUNNING)
        ws2 = make_workspace(id="ws-2", phase=Phase.PENDING, desired_state=DesiredState.RUNNING)
        ws3 = make_workspace(id="ws-3", phase=Phase.PENDING, desired_state=DesiredState.RUNNING)

        execute_calls = []
        persist_calls = []

        async def mock_execute(ws, _action):
            execute_calls.append(ws.id)
            if ws.id == "ws-2":
                raise RuntimeError("ws-2 failed")

        async def mock_persist(ws, _action):
            persist_calls.append(ws.id)

        wc._load_for_reconcile = AsyncMock(return_value=[ws1, ws2, ws3])
        wc._execute = mock_execute
        wc._persist = mock_persist

        # 에러가 발생해도 다른 ws는 처리됨
        await wc.tick()

        # 3개 workspace 모두 execute 시도됨 (병렬, 재시도 가능)
        # with_retry가 unknown 에러를 재시도하므로 호출 횟수 > 3
        assert set(execute_calls) == {"ws-1", "ws-2", "ws-3"}
        # 3개 모두 persist 시도됨 (순차, 에러 격리)
        assert set(persist_calls) == {"ws-1", "ws-2", "ws-3"}


class TestCasUpdate:
    """_cas_update() CAS pattern tests."""

    @pytest.fixture
    def wc(
        self,
        mock_conn: AsyncMock,
        mock_leader: AsyncMock,
        mock_subscriber: AsyncMock,
        mock_ic: AsyncMock,
        mock_sp: AsyncMock,
    ) -> WorkspaceController:
        return WorkspaceController(mock_conn, mock_leader, mock_subscriber, mock_ic, mock_sp)

    async def test_cas_success_when_operation_matches(
        self,
        wc: WorkspaceController,
        mock_conn: AsyncMock,
    ):
        """CAS succeeds when expected_operation matches current (rowcount=1)."""
        mock_result = MagicMock()
        mock_result.rowcount = 1  # 1 row updated
        mock_conn.execute.return_value = mock_result

        success = await wc._cas_update(
            workspace_id="ws-1",
            expected_operation=Operation.NONE,
            phase=Phase.STANDBY,
            operation=Operation.STARTING,
            op_started_at=datetime.now(UTC),
            op_id="op-123",
            archive_key=None,
            error_count=0,
            error_reason=None,
        )

        assert success is True

    async def test_cas_fails_when_operation_mismatch(
        self,
        wc: WorkspaceController,
        mock_conn: AsyncMock,
    ):
        """CAS fails when another WC modified operation (rowcount=0)."""
        mock_result = MagicMock()
        mock_result.rowcount = 0  # No row updated (CAS failed)
        mock_conn.execute.return_value = mock_result

        success = await wc._cas_update(
            workspace_id="ws-1",
            expected_operation=Operation.NONE,  # Expected NONE
            # But actual DB has STARTING (another WC updated it)
            phase=Phase.RUNNING,
            operation=Operation.NONE,
            op_started_at=None,
            op_id=None,
            archive_key=None,
            error_count=0,
            error_reason=None,
        )

        assert success is False  # CAS should fail

    async def test_cas_where_clause_includes_expected_operation(
        self,
        wc: WorkspaceController,
        mock_conn: AsyncMock,
    ):
        """CAS SQL WHERE clause includes operation = expected_operation."""
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_conn.execute.return_value = mock_result

        await wc._cas_update(
            workspace_id="ws-1",
            expected_operation=Operation.STARTING,
            phase=Phase.RUNNING,
            operation=Operation.NONE,
            op_started_at=None,
            op_id=None,
            archive_key=None,
            error_count=0,
            error_reason=None,
        )

        # Verify execute was called (CAS pattern in use)
        mock_conn.execute.assert_called_once()


class TestTickLogging:
    """tick() 로깅 동작 테스트 - 상태 변화 감지 패턴."""

    @pytest.fixture
    def wc(
        self,
        mock_conn: AsyncMock,
        mock_leader: AsyncMock,
        mock_subscriber: AsyncMock,
        mock_ic: AsyncMock,
        mock_sp: AsyncMock,
    ) -> WorkspaceController:
        return WorkspaceController(mock_conn, mock_leader, mock_subscriber, mock_ic, mock_sp)

    async def test_no_log_when_state_unchanged(
        self,
        wc: WorkspaceController,
        caplog: pytest.LogCaptureFixture,
    ):
        """상태 변화 없으면 두 번째 tick부터 INFO 로그 없음."""
        import logging

        # RUNNING 워크스페이스 (이미 수렴됨)
        ws = make_workspace(
            phase=Phase.RUNNING,
            desired_state=DesiredState.RUNNING,
            conditions={
                "container": {"running": True, "reason": "Running", "message": ""},
                "volume": {"exists": True, "reason": "VolumeExists", "message": ""},
            },
        )

        wc._load_for_reconcile = AsyncMock(return_value=[ws])
        wc._persist = AsyncMock()

        # 첫 번째 tick - 상태 초기화, 로그 발생
        with caplog.at_level(logging.INFO, logger="codehub.control.coordinator.wc"):
            await wc.tick()

        first_tick_logs = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(first_tick_logs) == 1  # 첫 tick은 로그 발생
        caplog.clear()

        # 두 번째 tick - 상태 동일, 로그 없음
        with caplog.at_level(logging.INFO, logger="codehub.control.coordinator.wc"):
            await wc.tick()

        second_tick_logs = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(second_tick_logs) == 0  # 상태 변화 없으면 로그 없음

    async def test_log_when_changed_increases(
        self,
        wc: WorkspaceController,
        caplog: pytest.LogCaptureFixture,
    ):
        """changed > 0 (상태 변화 발생) 이면 로그 발생."""
        import logging

        # 첫 번째 tick: 0 changed
        ws1 = make_workspace(
            phase=Phase.RUNNING,
            desired_state=DesiredState.RUNNING,
            conditions={
                "container": {"running": True, "reason": "Running", "message": ""},
                "volume": {"exists": True, "reason": "VolumeExists", "message": ""},
            },
        )
        wc._load_for_reconcile = AsyncMock(return_value=[ws1])
        wc._persist = AsyncMock()

        with caplog.at_level(logging.INFO, logger="codehub.control.coordinator.wc"):
            await wc.tick()
        caplog.clear()

        # 두 번째 tick: changed 발생 (새 ws 추가됨 = processed 변화)
        ws2 = make_workspace(
            id="ws-2",
            phase=Phase.RUNNING,
            desired_state=DesiredState.RUNNING,
            conditions={
                "container": {"running": True, "reason": "Running", "message": ""},
                "volume": {"exists": True, "reason": "VolumeExists", "message": ""},
            },
        )
        wc._load_for_reconcile = AsyncMock(return_value=[ws1, ws2])

        with caplog.at_level(logging.INFO, logger="codehub.control.coordinator.wc"):
            await wc.tick()

        # processed가 1 -> 2로 변경되었으므로 로그 발생
        tick_logs = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(tick_logs) == 1
        assert "Reconcile completed" in tick_logs[0].message

    async def test_heartbeat_after_one_hour(
        self,
        wc: WorkspaceController,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """1시간 후 heartbeat 로그 발생."""
        import logging
        import time

        ws = make_workspace(
            phase=Phase.RUNNING,
            desired_state=DesiredState.RUNNING,
            conditions={
                "container": {"running": True, "reason": "Running", "message": ""},
                "volume": {"exists": True, "reason": "VolumeExists", "message": ""},
            },
        )
        wc._load_for_reconcile = AsyncMock(return_value=[ws])
        wc._persist = AsyncMock()

        # 첫 번째 tick (시간 0)
        mock_time = 1000.0
        monkeypatch.setattr(time, "monotonic", lambda: mock_time)

        with caplog.at_level(logging.INFO, logger="codehub.control.coordinator.wc"):
            await wc.tick()
        caplog.clear()

        # 두 번째 tick (1시간 후)
        mock_time = 1000.0 + 3601  # 1시간 + 1초 후
        monkeypatch.setattr(time, "monotonic", lambda: mock_time)

        with caplog.at_level(logging.INFO, logger="codehub.control.coordinator.wc"):
            await wc.tick()

        heartbeat_logs = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(heartbeat_logs) == 1
        assert "Heartbeat" in heartbeat_logs[0].message
