"""WorkspaceController - 워크스페이스 상태 수렴.

설계: docs/architecture_v2/wc.md, wc-judge.md

WC = Judge + Control (Observer 분리)
- Observer Coordinator가 conditions 저장
- WC는 DB에서 conditions 읽어서 phase 계산 + operation 실행

Configuration via CoordinatorConfig (COORDINATOR_ env prefix).
"""

import asyncio
import logging
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.app.config import get_settings
from codehub.app.logging import clear_trace_context, set_trace_id
from codehub.app.metrics.collector import (
    INFRA_OPERATION_DURATION,
    OPERATION_FAILURES_TOTAL,
    OPERATION_TOTAL,
    RECONCILE_STAGE_DURATION,
)
from codehub.control.coordinator.base import (
    ChannelSubscriber,
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
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
from codehub.core.models import Workspace
from codehub.core.logging_schema import LogEvent
from codehub.core.retryable import classify_error, with_retry

logger = logging.getLogger(__name__)

# Module-level settings cache (consistent with base.py pattern)
_settings = get_settings()
_coordinator_config = _settings.coordinator
_logging_config = _settings.logging


class PlanAction(BaseModel):
    """Plan 단계 결과."""

    operation: Operation
    phase: Phase
    error_reason: ErrorReason | None = None
    archive_key: str | None = None
    op_id: str | None = None
    complete: bool = False  # operation 완료 여부
    restore_marker: str | None = None  # restore 완료 확인용 marker


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
    WAKE_TARGET = "wc"

    # Operation timeout from config
    OPERATION_TIMEOUT = _coordinator_config.operation_timeout

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        subscriber: ChannelSubscriber,
        ic: InstanceController,
        sp: StorageProvider,
    ) -> None:
        super().__init__(conn, leader, subscriber)
        self._ic = ic
        self._sp = sp

    async def tick(self) -> None:
        """Reconcile loop: Load → Judge → Plan → Execute → Persist.

        Hybrid execution strategy (ADR-012):
        - DB operations: Sequential (asyncpg single connection limit)
        - External operations (Docker/S3): Parallel for performance
        """
        # Generate tick-scoped trace_id for log correlation
        tick_id = str(uuid4())[:8]
        set_trace_id(tick_id)

        try:
            tick_start = time.monotonic()
            coordinator_name = self.COORDINATOR_TYPE.value

            # Stage 1: Load (DB)
            load_start = time.monotonic()
            workspaces = await self._load_for_reconcile()
            load_duration = time.monotonic() - load_start
            load_ms = load_duration * 1000
            RECONCILE_STAGE_DURATION.labels(coordinator=coordinator_name, stage="load").observe(load_duration)

            if not workspaces:
                return  # No reconciliation needed - skip logging for idle state

            # Stage 2: Judge + Plan (CPU)
            plan_start = time.monotonic()
            plans: list[tuple[Workspace, PlanAction]] = []
            for ws in workspaces:
                action = self._judge_and_plan(ws)
                plans.append((ws, action))
            plan_duration = time.monotonic() - plan_start
            plan_ms = plan_duration * 1000
            RECONCILE_STAGE_DURATION.labels(coordinator=coordinator_name, stage="plan").observe(plan_duration)

            # Stage 3: Execute 병렬 (Docker/S3 - DB 미사용!)
            exec_start = time.monotonic()

            async def execute_one(
                ws: Workspace, action: PlanAction
            ) -> tuple[Workspace, PlanAction]:
                if self._needs_execute(action, ws):
                    op_name = action.operation.value
                    try:
                        # with_retry handles transient errors with exponential backoff
                        await asyncio.wait_for(
                            with_retry(
                                lambda ws=ws, action=action: self._execute(ws, action),
                                max_retries=3,
                                base_delay=1.0,
                                max_delay=30.0,
                                circuit_breaker="external",
                            ),
                            timeout=self.OPERATION_TIMEOUT,
                        )
                        OPERATION_TOTAL.labels(operation=op_name, status="success").inc()
                    except asyncio.TimeoutError:
                        OPERATION_TOTAL.labels(operation=op_name, status="error").inc()
                        OPERATION_FAILURES_TOTAL.labels(operation=op_name, error_class="timeout").inc()
                        logger.error(
                            "Operation timeout",
                            extra={
                                "event": LogEvent.OPERATION_TIMEOUT,
                                "ws_id": ws.id,
                                "operation": op_name,
                                "error_class": "timeout",
                                "timeout_s": self.OPERATION_TIMEOUT,
                            },
                        )
                    except Exception as exc:
                        error_class = classify_error(exc)
                        OPERATION_TOTAL.labels(operation=op_name, status="error").inc()
                        OPERATION_FAILURES_TOTAL.labels(operation=op_name, error_class=error_class).inc()
                        logger.exception(
                            "Operation failed",
                            extra={
                                "event": LogEvent.OPERATION_FAILED,
                                "ws_id": ws.id,
                                "operation": op_name,
                                "error_class": error_class,
                                "retryable": error_class == "transient",
                            },
                        )
                return (ws, action)

            results = await asyncio.gather(
                *[execute_one(ws, action) for ws, action in plans],
                return_exceptions=False,  # 개별 예외 처리됨
            )
            exec_duration = time.monotonic() - exec_start
            exec_ms = exec_duration * 1000
            RECONCILE_STAGE_DURATION.labels(coordinator=coordinator_name, stage="execute").observe(exec_duration)

            # Stage 4: Persist 순차 (DB - ADR-012 준수)
            persist_start = time.monotonic()
            action_counts: Counter[str] = Counter()
            for ws, action in results:
                try:
                    await self._persist(ws, action)
                    if action.operation != Operation.NONE:
                        action_counts[action.operation.value] += 1
                except Exception:
                    logger.exception(
                        "Failed to persist",
                        extra={
                            "event": LogEvent.OPERATION_FAILED,
                            "ws_id": ws.id,
                            "operation": action.operation.value,
                            "error_class": "transient",
                        },
                    )
            persist_duration = time.monotonic() - persist_start
            persist_ms = persist_duration * 1000
            RECONCILE_STAGE_DURATION.labels(coordinator=coordinator_name, stage="persist").observe(persist_duration)

            # Log reconcile result with structured fields and stage durations
            duration_ms = (time.monotonic() - tick_start) * 1000
            logger.info(
                "Reconcile completed",
                extra={
                    "event": LogEvent.RECONCILE_COMPLETE,
                    "tick_id": tick_id,
                    "processed": len(workspaces),
                    "changed": sum(action_counts.values()),
                    "actions": dict(action_counts) if action_counts else {},
                    "duration_ms": duration_ms,
                    "load_ms": load_ms,
                    "plan_ms": plan_ms,
                    "exec_ms": exec_ms,
                    "persist_ms": persist_ms,
                },
            )

            # Slow reconcile warning (SLO threat detection)
            if duration_ms > _logging_config.slow_threshold_ms:
                logger.warning(
                    "Slow reconcile detected",
                    extra={
                        "event": LogEvent.RECONCILE_SLOW,
                        "tick_id": tick_id,
                        "duration_ms": duration_ms,
                        "threshold_ms": _logging_config.slow_threshold_ms,
                        "processed": len(workspaces),
                        "load_ms": load_ms,
                        "plan_ms": plan_ms,
                        "exec_ms": exec_ms,
                        "persist_ms": persist_ms,
                    },
                )
        finally:
            clear_trace_context()

    def _judge_and_plan(self, ws: Workspace) -> PlanAction:
        """Judge + Plan (순수 계산, DB 미사용)."""
        cond_input = ConditionInput.from_conditions(ws.conditions or {})
        judge_input = JudgeInput(
            conditions=cond_input,
            deleted_at=ws.deleted_at is not None,
        )
        judge_output = judge(judge_input)
        return self._plan(ws, judge_output)

    def _needs_execute(self, action: PlanAction, ws: Workspace) -> bool:
        """Execute 필요 여부 판단."""
        if action.operation == Operation.NONE or action.complete:
            return False
        # 새 operation 시작 또는 재시도
        return ws.operation == Operation.NONE or ws.operation == action.operation

    def _plan(self, ws: Workspace, judge_output: JudgeOutput) -> PlanAction:
        """operation 결정 로직.

        Cases:
        1. operation != NONE → 완료 조건 체크
        2. phase == ERROR → 대기 (또는 DELETING)
        3. phase == desired → no-op
        4. phase != desired → operation 선택
        """
        ws_op = ws.operation
        ws_desired = ws.desired_state

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
        ws_op = ws.operation

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
                # Backward compatible: marker 있으면 추가 검증
                if ws.home_ctx and ws.home_ctx.get("restore_marker"):
                    return cond.volume_ready and ws.home_ctx["restore_marker"] == ws.archive_key
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
                start = time.monotonic()
                await self._sp.provision(ws.id)
                INFRA_OPERATION_DURATION.labels(infra="s3", operation="provision").observe(
                    time.monotonic() - start
                )

            case Operation.RESTORING:
                if ws.archive_key:
                    start = time.monotonic()
                    await self._sp.restore(ws.id, ws.archive_key)
                    INFRA_OPERATION_DURATION.labels(infra="s3", operation="restore").observe(
                        time.monotonic() - start
                    )
                    action.restore_marker = ws.archive_key  # 완료 확인용 marker

            case Operation.STARTING:
                start = time.monotonic()
                await self._ic.start(ws.id, ws.image_ref)
                INFRA_OPERATION_DURATION.labels(infra="docker", operation="start").observe(
                    time.monotonic() - start
                )

            case Operation.STOPPING:
                start = time.monotonic()
                await self._ic.delete(ws.id)
                INFRA_OPERATION_DURATION.labels(infra="docker", operation="delete").observe(
                    time.monotonic() - start
                )

            case Operation.ARCHIVING:
                # 3단계 operation: archive → delete container → delete_volume
                # 컨테이너 삭제 추가: Exited 컨테이너도 볼륨 참조하므로 먼저 삭제 필요
                op_id = action.op_id or ws.op_id or str(uuid4())

                start = time.monotonic()
                archive_key = await self._sp.archive(ws.id, op_id)
                INFRA_OPERATION_DURATION.labels(infra="s3", operation="archive").observe(
                    time.monotonic() - start
                )
                action.archive_key = archive_key

                start = time.monotonic()
                await self._ic.delete(ws.id)  # Exited 컨테이너 정리 (idempotent)
                INFRA_OPERATION_DURATION.labels(infra="docker", operation="delete").observe(
                    time.monotonic() - start
                )

                start = time.monotonic()
                await self._sp.delete_volume(ws.id)
                INFRA_OPERATION_DURATION.labels(infra="s3", operation="delete_volume").observe(
                    time.monotonic() - start
                )

            case Operation.CREATE_EMPTY_ARCHIVE:
                op_id = action.op_id or ws.op_id or str(uuid4())
                start = time.monotonic()
                archive_key = await self._sp.create_empty_archive(ws.id, op_id)
                INFRA_OPERATION_DURATION.labels(infra="s3", operation="create_empty_archive").observe(
                    time.monotonic() - start
                )
                action.archive_key = archive_key

            case Operation.DELETING:
                # 2단계 operation: delete container → delete_volume (계약 #8)
                start = time.monotonic()
                await self._ic.delete(ws.id)
                INFRA_OPERATION_DURATION.labels(infra="docker", operation="delete").observe(
                    time.monotonic() - start
                )

                start = time.monotonic()
                await self._sp.delete_volume(ws.id)
                INFRA_OPERATION_DURATION.labels(infra="s3", operation="delete_volume").observe(
                    time.monotonic() - start
                )

    async def _persist(self, ws: Workspace, action: PlanAction) -> None:
        """CAS 패턴으로 DB 저장.

        CAS 조건: operation = expected_op
        - 다른 WC 인스턴스가 동시에 처리하면 CAS 실패 → 다음 tick에서 재시도
        """
        ws_op = ws.operation
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

        # home_ctx 업데이트 (restore_marker 저장)
        home_ctx: dict | None = None
        if action.restore_marker:
            home_ctx = dict(ws.home_ctx) if ws.home_ctx else {}
            home_ctx["restore_marker"] = action.restore_marker

        success = await self._cas_update(
            workspace_id=ws.id,
            expected_operation=Operation(ws_op),
            phase=action.phase,
            operation=action.operation,
            op_started_at=op_started_at,
            op_id=op_id,
            archive_key=action.archive_key,
            error_count=error_count,
            error_reason=action.error_reason,
            home_ctx=home_ctx,
            updated_at=now,
        )
        # Commit at connection level
        await self._conn.commit()

        if not success:
            logger.debug(
                "CAS failed, will retry next tick",
                extra={"ws_id": ws.id, "expected_op": ws_op.value},
            )
        elif action.phase != Phase(ws.phase) or action.operation != Operation.NONE:
            # Log state changes (phase change or operation in progress)
            logger.info(
                "State changed",
                extra={
                    "event": LogEvent.STATE_CHANGED,
                    "ws_id": ws.id,
                    "phase_from": ws.phase.value if isinstance(ws.phase, Phase) else ws.phase,
                    "phase_to": action.phase.value,
                    "operation": action.operation.value,
                },
            )

    # =================================================================
    # DB Operations (WC-owned columns, CAS pattern)
    # =================================================================

    async def _load_for_reconcile(self) -> list[Workspace]:
        """Load workspaces needing reconciliation.

        Conditions:
        - operation != NONE (in progress)
        - OR phase != desired_state (needs convergence)
        - OR phase == RUNNING (always check - container may be deleted externally)
        """
        stmt = select(Workspace).where(
            Workspace.deleted_at.is_(None),
            or_(
                Workspace.operation != Operation.NONE.value,
                Workspace.phase != Workspace.desired_state,
                Workspace.phase == Phase.RUNNING.value,  # RUNNING은 항상 체크
            ),
        )
        result = await self._conn.execute(stmt)
        rows = result.mappings().all()
        return [Workspace.model_validate(dict(row)) for row in rows]

    async def _cas_update(
        self,
        workspace_id: str,
        expected_operation: Operation,
        phase: Phase,
        operation: Operation,
        op_started_at: datetime | None,
        op_id: str | None,
        archive_key: str | None,
        error_count: int,
        error_reason: ErrorReason | None,
        home_ctx: dict | None = None,
        updated_at: datetime | None = None,
    ) -> bool:
        """CAS update for WC-owned columns.

        CAS condition: current operation must match expected_operation.
        """
        values: dict[str, Any] = {
            "phase": phase.value,
            "operation": operation.value,
            "op_started_at": op_started_at,
            "op_id": op_id,
            "error_count": error_count,
            "error_reason": error_reason.value if error_reason else None,
            "updated_at": updated_at or datetime.now(UTC),
            # phase_changed_at: only update when phase actually changes
            "phase_changed_at": case(
                (Workspace.phase != phase.value, func.now()),
                else_=Workspace.phase_changed_at,
            ),
        }

        # Only update archive_key if provided
        if archive_key is not None:
            values["archive_key"] = archive_key

        # Only update home_ctx if provided (restore_marker 저장용)
        if home_ctx is not None:
            values["home_ctx"] = home_ctx

        stmt = (
            update(Workspace)
            .where(
                Workspace.id == workspace_id,
                Workspace.operation == expected_operation.value,
            )
            .values(**values)
            .returning(Workspace.id)
        )
        result = await self._conn.execute(stmt)
        return result.rowcount > 0
