"""WorkspaceController - 워크스페이스 상태 수렴.

설계: docs/architecture_v2/wc.md, wc-judge.md

핵심 원칙:
- Level-Triggered: DB에서 conditions 읽기 (Observer가 저장한 것)
- Single Writer: phase, operation 등만 저장 (conditions 제외)
- Ordered SM: step_up/step_down 순차 전이
- Non-preemptive: workspace당 동시에 1개 operation만
"""

import logging
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from codehub.control.coordinator.base import (
    Channel,
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
    NotifySubscriber,
)
from codehub.control.coordinator.judge import (
    COND_ARCHIVE_READY,
    COND_CONTAINER_READY,
    COND_VOLUME_READY,
    calculate_phase,
    is_condition_true,
)
from codehub.core.domain.workspace import (
    DesiredState,
    ErrorReason,
    Operation,
    Phase,
)
from codehub.infra.models import Workspace

logger = logging.getLogger(__name__)

# Timeout for operations (seconds)
OPERATION_TIMEOUT_SECONDS = 300  # 5 minutes


class ActionType(StrEnum):
    """Action result type."""

    START = "start"  # operation 시작
    COMPLETE = "complete"  # operation 완료
    ERROR = "error"  # ERROR 전환
    CONTINUE = "continue"  # 진행 중 (아무것도 안 함)


class Action(BaseModel):
    """Reconcile action result."""

    type: ActionType
    operation: Operation | None = None
    op_id: str | None = None
    error_reason: ErrorReason | None = None
    archive_key: str | None = None  # ARCHIVING 완료 시 설정

    model_config = {"frozen": True}

    @classmethod
    def start(cls, operation: Operation, op_id: str | None = None) -> "Action":
        """Create start action."""
        return cls(
            type=ActionType.START,
            operation=operation,
            op_id=op_id or str(uuid.uuid4()),
        )

    @classmethod
    def complete(cls, archive_key: str | None = None) -> "Action":
        """Create complete action."""
        return cls(type=ActionType.COMPLETE, archive_key=archive_key)

    @classmethod
    def error(cls, reason: ErrorReason) -> "Action":
        """Create error action."""
        return cls(type=ActionType.ERROR, error_reason=reason)

    @classmethod
    def cont(cls) -> "Action":
        """Create continue action (no-op)."""
        return cls(type=ActionType.CONTINUE)


class WorkspaceController(CoordinatorBase):
    """워크스페이스 상태 수렴 컨트롤러.

    Reconcile Loop:
    1. DB에서 conditions 읽기 (Observer가 저장)
    2. Judge: calculate_phase()
    3. Control: Plan → Execute
    4. Persist: phase, operation 등 저장 (CAS)

    Single Writer 컬럼:
    - 읽기: conditions, observed_at, desired_state, deleted_at
    - 쓰기: phase, operation, op_started_at, op_id, archive_key,
            error_count, error_reason, home_ctx
    """

    COORDINATOR_TYPE = CoordinatorType.WC
    CHANNELS = [Channel.WC_WAKE]

    IDLE_INTERVAL = 10.0
    ACTIVE_INTERVAL = 2.0

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        notify: NotifySubscriber,
    ) -> None:
        super().__init__(conn, leader, notify)

    async def tick(self) -> None:
        """Reconcile all workspaces: Judge → Control → Persist."""
        async with AsyncSession(bind=self._conn) as session:
            # Reconcile 대상 조회:
            # - phase != desired_state (수렴 필요)
            # - OR operation != NONE (진행 중)
            result = await session.execute(
                select(Workspace).where(
                    Workspace.deleted_at.is_(None),
                    or_(
                        Workspace.phase != Workspace.desired_state,
                        Workspace.operation != Operation.NONE.value,
                    ),
                )
            )
            workspaces = result.scalars().all()

            if workspaces:
                logger.debug("Reconciling %d workspaces", len(workspaces))

            for ws in workspaces:
                await self._reconcile_one(session, ws)

            await session.commit()

    async def _reconcile_one(self, session: AsyncSession, ws: Workspace) -> None:
        """Reconcile single workspace."""
        ws_id = str(ws.id)

        # 1. Judge: conditions → phase
        new_phase = calculate_phase(
            conditions=ws.conditions,
            deleted_at=ws.deleted_at is not None,
            archive_key=ws.archive_key,
        )

        # Phase 변경 로그
        if new_phase.value != ws.phase:
            logger.info("Workspace %s: phase %s → %s", ws_id, ws.phase, new_phase.value)

        # 2. Control: Plan
        action = self._plan(ws, new_phase)

        # 3. Control: Execute (stub)
        if action.type == ActionType.START:
            await self._execute(ws, action)

        # 4. Persist
        await self._persist(session, ws, new_phase, action)

    def _plan(self, ws: Workspace, current_phase: Phase) -> Action:
        """Plan next action based on phase and desired_state.

        Priority:
        1. 진행 중인 operation → 완료 체크
        2. ERROR → DELETED만 허용
        3. 수렴 완료 → no-op
        4. Ordered SM → step_up/step_down
        """
        desired = DesiredState(ws.desired_state)
        operation = Operation(ws.operation)

        # 1. 진행 중인 operation 완료 체크
        if operation != Operation.NONE:
            return self._check_completion(ws, current_phase, operation)

        # 2. ERROR면 DELETED만 허용
        if current_phase == Phase.ERROR:
            if desired == DesiredState.DELETED:
                return Action.start(Operation.DELETING)
            # 수동 복구 대기
            return Action.cont()

        # 3. 수렴 완료
        if current_phase.value == desired.value:
            return Action.cont()

        # 4. Ordered SM: step_up / step_down
        return self._select_operation(current_phase, desired, ws)

    def _select_operation(
        self, phase: Phase, desired: DesiredState, _ws: Workspace
    ) -> Action:
        """Select operation based on Ordered State Machine.

        Phase Level: PENDING(0) < ARCHIVED(5) < STANDBY(10) < RUNNING(20)

        step_up: PENDING → ARCHIVED/STANDBY → RUNNING
        step_down: RUNNING → STANDBY → ARCHIVED
        """
        # step_up
        if phase == Phase.PENDING:
            if desired == DesiredState.ARCHIVED:
                return Action.start(Operation.CREATE_EMPTY_ARCHIVE)
            # STANDBY or RUNNING
            return Action.start(Operation.PROVISIONING)

        if phase == Phase.ARCHIVED:
            # ARCHIVED → STANDBY (then → RUNNING if needed)
            return Action.start(Operation.RESTORING)

        if phase == Phase.STANDBY:
            if desired == DesiredState.RUNNING:
                return Action.start(Operation.STARTING)
            if desired == DesiredState.ARCHIVED:
                return Action.start(Operation.ARCHIVING)

        # step_down
        if phase == Phase.RUNNING:
            # RUNNING → STANDBY (then → ARCHIVED if needed)
            return Action.start(Operation.STOPPING)

        # DELETING / DELETED 처리
        if phase == Phase.DELETING:
            return Action.start(Operation.DELETING)

        return Action.cont()

    def _check_completion(
        self, ws: Workspace, _phase: Phase, operation: Operation
    ) -> Action:
        """Check if current operation is complete.

        완료 조건은 Observer가 저장한 conditions 기반.
        """
        conditions = ws.conditions

        match operation:
            case Operation.PROVISIONING:
                if is_condition_true(conditions, COND_VOLUME_READY):
                    return Action.complete()

            case Operation.RESTORING:
                home_ctx = ws.home_ctx or {}
                if (
                    is_condition_true(conditions, COND_VOLUME_READY)
                    and home_ctx.get("restore_marker") == ws.archive_key
                ):
                    return Action.complete()

            case Operation.STARTING:
                if is_condition_true(conditions, COND_CONTAINER_READY):
                    return Action.complete()

            case Operation.STOPPING:
                if not is_condition_true(conditions, COND_CONTAINER_READY):
                    return Action.complete()

            case Operation.ARCHIVING:
                if (
                    not is_condition_true(conditions, COND_VOLUME_READY)
                    and is_condition_true(conditions, COND_ARCHIVE_READY)
                    and ws.archive_key
                ):
                    return Action.complete()

            case Operation.CREATE_EMPTY_ARCHIVE:
                if is_condition_true(conditions, COND_ARCHIVE_READY) and ws.archive_key:
                    return Action.complete()

            case Operation.DELETING:
                if (
                    not is_condition_true(conditions, COND_CONTAINER_READY)
                    and not is_condition_true(conditions, COND_VOLUME_READY)
                ):
                    return Action.complete()

        # Timeout 체크
        if self._is_timeout(ws.op_started_at):
            logger.warning(
                "Workspace %s: operation %s timeout", ws.id, operation.value
            )
            return Action.error(ErrorReason.TIMEOUT)

        # 아직 진행 중
        return Action.cont()

    def _is_timeout(self, op_started_at: datetime | None) -> bool:
        """Check if operation has timed out."""
        if op_started_at is None:
            return False
        elapsed = (datetime.now(UTC) - op_started_at).total_seconds()
        return elapsed > OPERATION_TIMEOUT_SECONDS

    async def _execute(self, ws: Workspace, action: Action) -> None:
        """Execute action (stub - log only).

        실제 Actuator 구현체는 별도 작업으로 진행.
        """
        ws_id = str(ws.id)

        match action.operation:
            case Operation.PROVISIONING:
                logger.info("[STUB] SP.provision(%s)", ws_id)

            case Operation.RESTORING:
                logger.info("[STUB] SP.restore(%s, %s)", ws_id, ws.archive_key)

            case Operation.STARTING:
                logger.info("[STUB] IC.start(%s, %s)", ws_id, ws.image_ref)

            case Operation.STOPPING:
                logger.info("[STUB] IC.delete(%s)", ws_id)

            case Operation.ARCHIVING:
                logger.info("[STUB] SP.archive(%s, %s)", ws_id, action.op_id)

            case Operation.CREATE_EMPTY_ARCHIVE:
                logger.info(
                    "[STUB] SP.create_empty_archive(%s, %s)", ws_id, action.op_id
                )

            case Operation.DELETING:
                logger.info("[STUB] IC.delete + SP.delete_volume(%s)", ws_id)

    async def _persist(
        self,
        session: AsyncSession,
        ws: Workspace,
        new_phase: Phase,
        action: Action,
    ) -> None:
        """Persist changes to DB with CAS pattern.

        Single Writer: phase, operation, op_started_at, op_id,
                       archive_key, error_count, error_reason, home_ctx
        """
        ws_id = str(ws.id)
        now = datetime.now(UTC)

        # 기본 업데이트 값
        values: dict = {"phase": new_phase.value}

        match action.type:
            case ActionType.START:
                # operation 시작
                values.update(
                    {
                        "operation": action.operation.value,
                        "op_started_at": now,
                        "op_id": action.op_id,
                        "error_count": 0,
                        "error_reason": None,
                    }
                )
                logger.info(
                    "Workspace %s: starting %s (op_id=%s)",
                    ws_id,
                    action.operation.value,
                    action.op_id,
                )

            case ActionType.COMPLETE:
                # operation 완료
                values.update(
                    {
                        "operation": Operation.NONE.value,
                        "op_started_at": None,
                        # op_id 유지 (GC 보호)
                        "error_count": 0,
                        "error_reason": None,
                    }
                )
                if action.archive_key:
                    values["archive_key"] = action.archive_key
                logger.info(
                    "Workspace %s: completed %s", ws_id, ws.operation
                )

            case ActionType.ERROR:
                # ERROR 전환 (원자적)
                values.update(
                    {
                        "phase": Phase.ERROR.value,
                        "operation": Operation.NONE.value,
                        "error_reason": action.error_reason.value,
                        "error_count": ws.error_count + 1,
                    }
                )
                logger.warning(
                    "Workspace %s: ERROR (%s)", ws_id, action.error_reason.value
                )

            case ActionType.CONTINUE:
                # 진행 중 또는 no-op
                pass

        # CAS 패턴: operation 변경 시 이전 상태 확인
        result = await session.execute(
            update(Workspace)
            .where(
                Workspace.id == ws_id,
                Workspace.operation == ws.operation,  # CAS 조건
            )
            .values(**values)
            .returning(Workspace.id)
        )

        if not result.fetchone():
            # CAS 실패 → 다음 tick에서 재시도
            logger.warning("Workspace %s: CAS failed, will retry", ws_id)
