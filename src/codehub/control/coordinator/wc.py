"""WorkspaceController - 워크스페이스 상태 수렴.

설계: docs/architecture_v2/wc.md, wc-judge.md

WC = Judge + Control (Observer 분리)
- Observer Coordinator가 conditions 저장
- WC는 DB에서 conditions 읽어서 phase 계산 + operation 실행
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlmodel.ext.asyncio.session import AsyncSession

from codehub.control.coordinator.base import (
    Channel,
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
    NotifySubscriber,
)
from codehub.control.coordinator.judge import JudgeInput, JudgeOutput, judge
from codehub.core.domain.conditions import ConditionInput
from codehub.core.domain.workspace import (
    DesiredState,
    ErrorReason,
    Operation,
    Phase,
)
from codehub.core.interfaces.instance import InstanceController
from codehub.core.interfaces.storage import StorageProvider
from codehub.infra.models import Workspace

logger = logging.getLogger(__name__)


@dataclass
class PlanAction:
    """Plan 단계 결과."""

    operation: Operation
    phase: Phase
    error_reason: ErrorReason | None = None
    archive_key: str | None = None
    op_id: str | None = None
    complete: bool = False  # operation 완료 여부


class WorkspaceController(CoordinatorBase):
    """워크스페이스 상태 수렴 컨트롤러.

    Reconcile Loop:
    1. Load: DB에서 workspace 목록 로드 (conditions 포함)
    2. Judge: judge() 호출 → phase 계산
    3. Plan: operation 결정
    4. Execute: Actuator 호출
    5. Persist: CAS 패턴으로 DB 저장
    """

    COORDINATOR_TYPE = CoordinatorType.WC
    CHANNELS = [Channel.WC_WAKE]

    IDLE_INTERVAL = 10.0
    ACTIVE_INTERVAL = 2.0

    # Operation timeout (초)
    OPERATION_TIMEOUT = 300  # 5분

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        notify: NotifySubscriber,
        ic: InstanceController,
        sp: StorageProvider,
    ) -> None:
        super().__init__(conn, leader, notify)
        self._ic = ic
        self._sp = sp

    async def tick(self) -> None:
        """Reconcile loop: Load → Judge → Plan → Execute → Persist."""
        workspaces = await self._load_workspaces()

        # 병렬 실행 (return_exceptions=True로 한 ws 실패해도 나머지 계속)
        results = await asyncio.gather(
            *[self._reconcile_one(ws) for ws in workspaces],
            return_exceptions=True,
        )

        # 에러 로깅
        for ws, result in zip(workspaces, results):
            if isinstance(result, Exception):
                logger.exception("Failed to reconcile %s: %s", ws.id, result)

    async def _load_workspaces(self) -> list[Workspace]:
        """Load workspaces that need reconciliation.

        조건:
        - operation != NONE (진행 중)
        - 또는 phase != desired_state (수렴 필요)
        """
        async with AsyncSession(bind=self._conn) as session:
            result = await session.execute(
                text("""
                    SELECT * FROM workspaces
                    WHERE deleted_at IS NULL
                      AND (
                        operation != 'NONE'
                        OR phase != desired_state
                      )
                """)
            )
            rows = result.mappings().all()
            return [Workspace.model_validate(dict(row)) for row in rows]

    async def _reconcile_one(self, ws: Workspace) -> None:
        """Reconcile single workspace."""
        # 1. Judge
        cond_input = ConditionInput.from_conditions(ws.conditions or {})
        judge_input = JudgeInput(
            conditions=cond_input,
            deleted_at=ws.deleted_at is not None,
            archive_key=ws.archive_key,
        )
        judge_output = judge(judge_input)

        # 2. Plan
        action = self._plan(ws, judge_output)

        # 3. Execute (operation 시작 시에만)
        if action.operation != Operation.NONE and not action.complete:
            # 새 operation 시작 또는 재시도
            if ws.operation == Operation.NONE or ws.operation == action.operation:
                await self._execute(ws, action)

        # 4. Persist (CAS)
        await self._persist(ws, action)

    def _plan(self, ws: Workspace, judge_output: JudgeOutput) -> PlanAction:
        """operation 결정 로직.

        Cases:
        1. operation != NONE → 완료 조건 체크
        2. phase == ERROR → 대기 (또는 DELETING)
        3. phase == desired → no-op
        4. phase != desired → operation 선택
        """
        ws_op = Operation(ws.operation)
        ws_desired = DesiredState(ws.desired_state)

        # Case 1: 진행 중인 operation
        if ws_op != Operation.NONE:
            return self._handle_in_progress(ws, judge_output)

        # Case 2: ERROR 처리
        if judge_output.phase == Phase.ERROR:
            if ws_desired == DesiredState.DELETED:
                return PlanAction(
                    operation=Operation.DELETING,
                    phase=Phase.DELETING,
                    op_id=str(uuid4()),
                )
            # ERROR 상태 유지 (수동 복구 필요)
            return PlanAction(
                operation=Operation.NONE,
                phase=Phase.ERROR,
                error_reason=judge_output.error_reason,
            )

        # Case 3: 이미 수렴됨
        target_phase = self._phase_from_desired(ws_desired)
        if judge_output.phase == target_phase:
            return PlanAction(
                operation=Operation.NONE,
                phase=judge_output.phase,
            )

        # Case 4: operation 선택
        operation = self._select_operation(judge_output.phase, ws_desired)
        if operation == Operation.NONE:
            return PlanAction(
                operation=Operation.NONE,
                phase=judge_output.phase,
            )

        return PlanAction(
            operation=operation,
            phase=judge_output.phase,
            op_id=str(uuid4()),
        )

    def _handle_in_progress(self, ws: Workspace, judge_output: JudgeOutput) -> PlanAction:
        """진행 중인 operation 처리.

        완료 조건:
        - PROVISIONING: volume_ready
        - RESTORING: volume_ready
        - STARTING: container_ready
        - STOPPING: !container_ready
        - ARCHIVING: !volume_ready ∧ archive_ready
        - CREATE_EMPTY_ARCHIVE: archive_ready
        - DELETING: !container_ready ∧ !volume_ready
        """
        ws_op = Operation(ws.operation)

        # 완료 조건 체크
        complete = self._check_completion(ws_op, ws)

        if complete:
            # 완료 → phase 재계산, operation = NONE
            return PlanAction(
                operation=Operation.NONE,
                phase=judge_output.phase,
                complete=True,
            )

        # Timeout 체크
        if ws.op_started_at and self._is_timeout(ws.op_started_at):
            return PlanAction(
                operation=Operation.NONE,
                phase=Phase.ERROR,
                error_reason=ErrorReason.TIMEOUT,
            )

        # 진행 중 → 재시도 (멱등)
        return PlanAction(
            operation=ws_op,
            phase=ws.phase,
            op_id=ws.op_id,
        )

    def _check_completion(self, operation: Operation, ws: Workspace) -> bool:
        """operation 완료 조건 체크."""
        cond = ConditionInput.from_conditions(ws.conditions or {})

        match operation:
            case Operation.PROVISIONING:
                return cond.volume_ready
            case Operation.RESTORING:
                return cond.volume_ready
            case Operation.STARTING:
                return cond.container_ready
            case Operation.STOPPING:
                return not cond.container_ready
            case Operation.ARCHIVING:
                return not cond.volume_ready and cond.archive_ready
            case Operation.CREATE_EMPTY_ARCHIVE:
                return cond.archive_ready
            case Operation.DELETING:
                return not cond.container_ready and not cond.volume_ready
            case _:
                return False

    def _is_timeout(self, op_started_at: datetime) -> bool:
        """operation timeout 체크."""
        elapsed = (datetime.now(UTC) - op_started_at).total_seconds()
        return elapsed > self.OPERATION_TIMEOUT

    def _phase_from_desired(self, desired: DesiredState) -> Phase:
        """DesiredState → 목표 Phase 변환."""
        match desired:
            case DesiredState.RUNNING:
                return Phase.RUNNING
            case DesiredState.STANDBY:
                return Phase.STANDBY
            case DesiredState.ARCHIVED:
                return Phase.ARCHIVED
            case DesiredState.DELETED:
                return Phase.DELETED
            case _:
                return Phase.PENDING

    def _select_operation(self, current_phase: Phase, desired: DesiredState) -> Operation:
        """현재 phase에서 desired로 가기 위한 operation 선택.

        Operation 선택 테이블 (wc.md):
        | Phase | desired | Operation |
        |-------|---------|-----------|
        | PENDING | ARCHIVED | CREATE_EMPTY_ARCHIVE |
        | PENDING | STANDBY/RUNNING | PROVISIONING |
        | ARCHIVED | STANDBY/RUNNING | RESTORING |
        | STANDBY | RUNNING | STARTING |
        | RUNNING | STANDBY/ARCHIVED | STOPPING |
        | STANDBY | ARCHIVED | ARCHIVING |
        | * | DELETED | DELETING |
        """
        # DELETED는 어디서든 DELETING
        if desired == DesiredState.DELETED:
            return Operation.DELETING

        match current_phase:
            case Phase.PENDING:
                if desired == DesiredState.ARCHIVED:
                    return Operation.CREATE_EMPTY_ARCHIVE
                if desired in (DesiredState.STANDBY, DesiredState.RUNNING):
                    return Operation.PROVISIONING

            case Phase.ARCHIVED:
                if desired in (DesiredState.STANDBY, DesiredState.RUNNING):
                    return Operation.RESTORING

            case Phase.STANDBY:
                if desired == DesiredState.RUNNING:
                    return Operation.STARTING
                if desired == DesiredState.ARCHIVED:
                    return Operation.ARCHIVING

            case Phase.RUNNING:
                if desired in (DesiredState.STANDBY, DesiredState.ARCHIVED):
                    return Operation.STOPPING

        return Operation.NONE

    async def _execute(self, ws: Workspace, action: PlanAction) -> None:
        """Actuator 호출.

        계약 #8: 순서 보장
        - ARCHIVING: archive() → delete_volume()
        - DELETING: delete() → delete_volume()
        """
        match action.operation:
            case Operation.PROVISIONING:
                await self._sp.provision(ws.id)

            case Operation.RESTORING:
                if ws.archive_key:
                    await self._sp.restore(ws.id, ws.archive_key)

            case Operation.STARTING:
                await self._ic.start(ws.id, ws.image_ref)

            case Operation.STOPPING:
                await self._ic.delete(ws.id)

            case Operation.ARCHIVING:
                # 2단계 operation: archive → delete_volume (계약 #8)
                op_id = action.op_id or ws.op_id or str(uuid4())
                archive_key = await self._sp.archive(ws.id, op_id)
                action.archive_key = archive_key
                await self._sp.delete_volume(ws.id)

            case Operation.CREATE_EMPTY_ARCHIVE:
                op_id = action.op_id or ws.op_id or str(uuid4())
                archive_key = await self._sp.create_empty_archive(ws.id, op_id)
                action.archive_key = archive_key

            case Operation.DELETING:
                # 2단계 operation: delete container → delete_volume (계약 #8)
                await self._ic.delete(ws.id)
                await self._sp.delete_volume(ws.id)

    async def _persist(self, ws: Workspace, action: PlanAction) -> None:
        """CAS 패턴으로 DB 저장.

        CAS 조건: operation = expected_op
        - 다른 WC 인스턴스가 동시에 처리하면 CAS 실패 → 다음 tick에서 재시도
        """
        ws_op = Operation(ws.operation)
        now = datetime.now(UTC)

        # operation 시작 시점 결정
        if action.operation != Operation.NONE and ws_op == Operation.NONE:
            # 새 operation 시작
            op_started_at = now
            op_id = action.op_id or str(uuid4())
        elif action.operation == Operation.NONE:
            # operation 완료 또는 no-op
            op_started_at = None
            op_id = ws.op_id  # GC 보호용 유지
        else:
            # 진행 중
            op_started_at = ws.op_started_at
            op_id = ws.op_id

        # error_count 계산
        if action.error_reason:
            error_count = ws.error_count + 1
        elif action.complete:
            error_count = 0  # 성공 완료 시 리셋
        else:
            error_count = ws.error_count

        async with AsyncSession(bind=self._conn) as session:
            result = await session.execute(
                text("""
                    UPDATE workspaces
                    SET phase = :phase,
                        operation = :operation,
                        op_started_at = :op_started_at,
                        op_id = :op_id,
                        archive_key = COALESCE(:archive_key, archive_key),
                        error_count = :error_count,
                        error_reason = :error_reason,
                        updated_at = :updated_at
                    WHERE id = :ws_id
                      AND operation = :expected_op
                    RETURNING id
                """),
                {
                    "ws_id": ws.id,
                    "phase": action.phase.value,
                    "operation": action.operation.value,
                    "op_started_at": op_started_at,
                    "op_id": op_id,
                    "archive_key": action.archive_key,
                    "error_count": error_count,
                    "error_reason": action.error_reason.value if action.error_reason else None,
                    "expected_op": ws_op.value,
                    "updated_at": now,
                },
            )
            await session.commit()

            if result.rowcount == 0:
                logger.info("CAS failed for %s (expected_op=%s), will retry next tick", ws.id, ws_op)
            else:
                logger.debug(
                    "Updated %s: phase=%s, operation=%s",
                    ws.id,
                    action.phase,
                    action.operation,
                )
