"""Tests for WorkspaceController.

Reference: docs/architecture_v2/wc.md
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub.control.coordinator.wc import PlanAction, WorkspaceController
from codehub.core.domain.workspace import (
    DesiredState,
    ErrorReason,
    Operation,
    Phase,
)
from codehub.core.interfaces.instance import InstanceController
from codehub.core.interfaces.storage import StorageProvider
from codehub.infra.models import Workspace


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
    return AsyncMock()


@pytest.fixture
def mock_leader() -> AsyncMock:
    """Mock LeaderElection."""
    leader = AsyncMock()
    leader.is_leader = True
    leader.try_acquire = AsyncMock(return_value=True)
    return leader


@pytest.fixture
def mock_notify() -> AsyncMock:
    """Mock NotifySubscriber."""
    notify = AsyncMock()
    notify.subscribe = AsyncMock()
    notify.unsubscribe = AsyncMock()
    notify.get_message = AsyncMock(return_value=None)
    return notify


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
    """_select_operation() 테스트."""

    @pytest.fixture
    def wc(
        self,
        mock_conn: AsyncMock,
        mock_leader: AsyncMock,
        mock_notify: AsyncMock,
        mock_ic: AsyncMock,
        mock_sp: AsyncMock,
    ) -> WorkspaceController:
        return WorkspaceController(mock_conn, mock_leader, mock_notify, mock_ic, mock_sp)

    def test_pending_to_running(self, wc: WorkspaceController):
        """PENDING → RUNNING: PROVISIONING."""
        op = wc._select_operation(Phase.PENDING, DesiredState.RUNNING)
        assert op == Operation.PROVISIONING

    def test_pending_to_standby(self, wc: WorkspaceController):
        """PENDING → STANDBY: PROVISIONING."""
        op = wc._select_operation(Phase.PENDING, DesiredState.STANDBY)
        assert op == Operation.PROVISIONING

    def test_pending_to_archived(self, wc: WorkspaceController):
        """PENDING → ARCHIVED: CREATE_EMPTY_ARCHIVE."""
        op = wc._select_operation(Phase.PENDING, DesiredState.ARCHIVED)
        assert op == Operation.CREATE_EMPTY_ARCHIVE

    def test_archived_to_running(self, wc: WorkspaceController):
        """ARCHIVED → RUNNING: RESTORING."""
        op = wc._select_operation(Phase.ARCHIVED, DesiredState.RUNNING)
        assert op == Operation.RESTORING

    def test_archived_to_standby(self, wc: WorkspaceController):
        """ARCHIVED → STANDBY: RESTORING."""
        op = wc._select_operation(Phase.ARCHIVED, DesiredState.STANDBY)
        assert op == Operation.RESTORING

    def test_standby_to_running(self, wc: WorkspaceController):
        """STANDBY → RUNNING: STARTING."""
        op = wc._select_operation(Phase.STANDBY, DesiredState.RUNNING)
        assert op == Operation.STARTING

    def test_standby_to_archived(self, wc: WorkspaceController):
        """STANDBY → ARCHIVED: ARCHIVING."""
        op = wc._select_operation(Phase.STANDBY, DesiredState.ARCHIVED)
        assert op == Operation.ARCHIVING

    def test_running_to_standby(self, wc: WorkspaceController):
        """RUNNING → STANDBY: STOPPING."""
        op = wc._select_operation(Phase.RUNNING, DesiredState.STANDBY)
        assert op == Operation.STOPPING

    def test_running_to_archived(self, wc: WorkspaceController):
        """RUNNING → ARCHIVED: STOPPING (step by step)."""
        op = wc._select_operation(Phase.RUNNING, DesiredState.ARCHIVED)
        assert op == Operation.STOPPING

    def test_any_to_deleted(self, wc: WorkspaceController):
        """Any phase → DELETED: DELETING."""
        for phase in [Phase.PENDING, Phase.ARCHIVED, Phase.STANDBY, Phase.RUNNING]:
            op = wc._select_operation(phase, DesiredState.DELETED)
            assert op == Operation.DELETING


class TestCheckCompletion:
    """_check_completion() 테스트."""

    @pytest.fixture
    def wc(
        self,
        mock_conn: AsyncMock,
        mock_leader: AsyncMock,
        mock_notify: AsyncMock,
        mock_ic: AsyncMock,
        mock_sp: AsyncMock,
    ) -> WorkspaceController:
        return WorkspaceController(mock_conn, mock_leader, mock_notify, mock_ic, mock_sp)

    def test_provisioning_complete(self, wc: WorkspaceController):
        """PROVISIONING 완료: volume_ready=True."""
        ws = make_workspace(
            conditions={"volume": {"exists": True, "reason": "VolumeExists", "message": ""}}
        )
        assert wc._check_completion(Operation.PROVISIONING, ws) is True

    def test_provisioning_incomplete(self, wc: WorkspaceController):
        """PROVISIONING 미완료: volume_ready=False."""
        ws = make_workspace(conditions={})
        assert wc._check_completion(Operation.PROVISIONING, ws) is False

    def test_starting_complete(self, wc: WorkspaceController):
        """STARTING 완료: container_ready=True."""
        ws = make_workspace(
            conditions={
                "container": {"running": True, "reason": "Running", "message": ""},
                "volume": {"exists": True, "reason": "VolumeExists", "message": ""},
            }
        )
        assert wc._check_completion(Operation.STARTING, ws) is True

    def test_stopping_complete(self, wc: WorkspaceController):
        """STOPPING 완료: container_ready=False."""
        ws = make_workspace(
            conditions={"volume": {"exists": True, "reason": "VolumeExists", "message": ""}}
        )
        assert wc._check_completion(Operation.STOPPING, ws) is True

    def test_archiving_complete(self, wc: WorkspaceController):
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
        assert wc._check_completion(Operation.ARCHIVING, ws) is True

    def test_deleting_complete(self, wc: WorkspaceController):
        """DELETING 완료: !container_ready ∧ !volume_ready."""
        ws = make_workspace(conditions={})
        assert wc._check_completion(Operation.DELETING, ws) is True


class TestPlan:
    """_plan() 테스트."""

    @pytest.fixture
    def wc(
        self,
        mock_conn: AsyncMock,
        mock_leader: AsyncMock,
        mock_notify: AsyncMock,
        mock_ic: AsyncMock,
        mock_sp: AsyncMock,
    ) -> WorkspaceController:
        return WorkspaceController(mock_conn, mock_leader, mock_notify, mock_ic, mock_sp)

    def test_already_converged(self, wc: WorkspaceController):
        """이미 수렴됨 → no-op."""
        ws = make_workspace(
            phase=Phase.RUNNING,
            desired_state=DesiredState.RUNNING,
            conditions={
                "container": {"running": True, "reason": "Running", "message": ""},
                "volume": {"exists": True, "reason": "VolumeExists", "message": ""},
            },
        )
        from codehub.control.coordinator.judge import JudgeInput, JudgeOutput, judge
        from codehub.core.domain.conditions import ConditionInput

        cond = ConditionInput.from_conditions(ws.conditions)
        judge_output = judge(JudgeInput(conditions=cond, deleted_at=False))

        action = wc._plan(ws, judge_output)

        assert action.operation == Operation.NONE
        assert action.phase == Phase.RUNNING

    def test_need_convergence(self, wc: WorkspaceController):
        """수렴 필요 → operation 선택."""
        ws = make_workspace(
            phase=Phase.PENDING,
            desired_state=DesiredState.RUNNING,
            conditions={},
        )
        from codehub.control.coordinator.judge import JudgeInput, JudgeOutput, judge
        from codehub.core.domain.conditions import ConditionInput

        cond = ConditionInput.from_conditions(ws.conditions)
        judge_output = judge(JudgeInput(conditions=cond, deleted_at=False))

        action = wc._plan(ws, judge_output)

        assert action.operation == Operation.PROVISIONING
        assert action.op_id is not None

    def test_operation_in_progress_complete(self, wc: WorkspaceController):
        """진행 중 operation 완료."""
        ws = make_workspace(
            phase=Phase.PENDING,
            operation=Operation.PROVISIONING,
            desired_state=DesiredState.RUNNING,
            conditions={"volume": {"exists": True, "reason": "VolumeExists", "message": ""}},
            op_started_at=datetime.now(UTC),
            op_id="op-1",
        )
        from codehub.control.coordinator.judge import JudgeInput, judge
        from codehub.core.domain.conditions import ConditionInput

        cond = ConditionInput.from_conditions(ws.conditions)
        judge_output = judge(JudgeInput(conditions=cond, deleted_at=False))

        action = wc._plan(ws, judge_output)

        assert action.operation == Operation.NONE
        assert action.complete is True

    def test_operation_timeout(self, wc: WorkspaceController):
        """operation timeout → ERROR."""
        ws = make_workspace(
            phase=Phase.PENDING,
            operation=Operation.PROVISIONING,
            desired_state=DesiredState.RUNNING,
            conditions={},
            op_started_at=datetime.now(UTC) - timedelta(seconds=400),
            op_id="op-1",
        )
        from codehub.control.coordinator.judge import JudgeInput, judge
        from codehub.core.domain.conditions import ConditionInput

        cond = ConditionInput.from_conditions(ws.conditions)
        judge_output = judge(JudgeInput(conditions=cond, deleted_at=False))

        action = wc._plan(ws, judge_output)

        assert action.operation == Operation.NONE
        assert action.phase == Phase.ERROR
        assert action.error_reason == ErrorReason.TIMEOUT

    def test_error_phase_deleted_desired(self, wc: WorkspaceController):
        """ERROR phase + desired=DELETED → DELETING."""
        ws = make_workspace(
            phase=Phase.ERROR,
            desired_state=DesiredState.DELETED,
            conditions={
                "container": {"running": True, "reason": "Running", "message": ""},
                # volume_ready=False → invariant violation
            },
        )
        from codehub.control.coordinator.judge import JudgeInput, judge
        from codehub.core.domain.conditions import ConditionInput

        cond = ConditionInput.from_conditions(ws.conditions)
        judge_output = judge(JudgeInput(conditions=cond, deleted_at=False))

        action = wc._plan(ws, judge_output)

        assert action.operation == Operation.DELETING
        assert action.phase == Phase.DELETING


class TestExecute:
    """_execute() 테스트."""

    @pytest.fixture
    def wc(
        self,
        mock_conn: AsyncMock,
        mock_leader: AsyncMock,
        mock_notify: AsyncMock,
        mock_ic: AsyncMock,
        mock_sp: AsyncMock,
    ) -> WorkspaceController:
        return WorkspaceController(mock_conn, mock_leader, mock_notify, mock_ic, mock_sp)

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

    async def test_archiving(self, wc: WorkspaceController, mock_sp: AsyncMock):
        """ARCHIVING → sp.archive() + sp.delete_volume()."""
        ws = make_workspace()
        action = PlanAction(operation=Operation.ARCHIVING, phase=Phase.STANDBY, op_id="op-1")

        await wc._execute(ws, action)

        mock_sp.archive.assert_called_once_with(ws.id, "op-1")
        mock_sp.delete_volume.assert_called_once_with(ws.id)
        assert action.archive_key == "ws-1/op-1/home.tar.zst"

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
    """_phase_from_desired() 테스트."""

    @pytest.fixture
    def wc(
        self,
        mock_conn: AsyncMock,
        mock_leader: AsyncMock,
        mock_notify: AsyncMock,
        mock_ic: AsyncMock,
        mock_sp: AsyncMock,
    ) -> WorkspaceController:
        return WorkspaceController(mock_conn, mock_leader, mock_notify, mock_ic, mock_sp)

    def test_running(self, wc: WorkspaceController):
        assert wc._phase_from_desired(DesiredState.RUNNING) == Phase.RUNNING

    def test_standby(self, wc: WorkspaceController):
        assert wc._phase_from_desired(DesiredState.STANDBY) == Phase.STANDBY

    def test_archived(self, wc: WorkspaceController):
        assert wc._phase_from_desired(DesiredState.ARCHIVED) == Phase.ARCHIVED

    def test_deleted(self, wc: WorkspaceController):
        assert wc._phase_from_desired(DesiredState.DELETED) == Phase.DELETED
