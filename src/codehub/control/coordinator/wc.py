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

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.app.config import get_settings
from codehub.app.logging import clear_trace_context, set_trace_id
from codehub.app.metrics.collector import (
    WC_CAS_FAILURES_TOTAL,
    WC_EXECUTE_DURATION,
    WC_OPERATION_DURATION,
    WC_STAGE_DURATION,
)
from codehub.control.coordinator.base import (
    ChannelSubscriber,
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
)
from codehub.control.coordinator.wc_planner import (
    PlanAction,
    PlanInput,
    needs_execute,
    plan,
)
from codehub.core.domain.workspace import (
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
        # Track previous state to log only on changes (reduces noise)
        self._prev_state: tuple[int, int] | None = None
        self._last_heartbeat: float = 0.0

    async def reconcile(self) -> None:
        """Reconcile loop: Load → Judge → Plan → Execute → Persist.

        Hybrid execution strategy (ADR-012):
        - DB operations: Sequential (asyncpg single connection limit)
        - External operations (Docker/S3): Parallel for performance

        Metrics strategy:
        - Always record metrics (even when idle) for continuous graphs
        - Record operation duration on both success and failure
        """
        # Generate reconcile-scoped trace_id for log correlation
        reconcile_id = str(uuid4())[:8]
        set_trace_id(reconcile_id)

        try:
            reconcile_start = time.monotonic()

            # Stage 1: Load (DB) - always measured
            load_start = time.monotonic()
            workspaces = await self._load_for_reconcile()
            load_duration = time.monotonic() - load_start
            load_ms = load_duration * 1000
            WC_STAGE_DURATION.labels(stage="load").observe(load_duration)

            # Initialize metrics variables for pass-through structure
            plan_duration = 0.0
            plan_ms = 0.0
            exec_duration = 0.0
            exec_ms = 0.0
            persist_duration = 0.0
            persist_ms = 0.0
            action_counts: Counter[str] = Counter()

            if workspaces:
                # Stage 2: Judge + Plan (CPU)
                plan_start = time.monotonic()
                plans: list[tuple[Workspace, PlanAction]] = []
                for ws in workspaces:
                    action = self._judge_and_plan(ws)
                    plans.append((ws, action))
                plan_duration = time.monotonic() - plan_start
                plan_ms = plan_duration * 1000

                # Stage 3: Execute 병렬 (Docker/S3 - DB 미사용!)
                exec_start = time.monotonic()
                results = await asyncio.gather(
                    *[self._execute_one(ws, action) for ws, action in plans],
                    return_exceptions=False,  # 개별 예외 처리됨
                )
                exec_duration = time.monotonic() - exec_start
                exec_ms = exec_duration * 1000

                # Stage 4: Persist 순차 (DB - ADR-012 준수)
                persist_start = time.monotonic()
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

            # Always record stage duration metrics (for continuous graphs)
            WC_STAGE_DURATION.labels(stage="plan").observe(plan_duration)
            WC_EXECUTE_DURATION.observe(exec_duration)
            WC_STAGE_DURATION.labels(stage="persist").observe(persist_duration)

            # Reconcile summary for logging (metrics removed - logs are sufficient)
            processed_count = len(workspaces)
            changed_count = sum(action_counts.values())

            # Log reconcile result only when state changes OR hourly heartbeat
            duration_ms = (time.monotonic() - reconcile_start) * 1000
            current_state = (processed_count, changed_count)
            now = time.monotonic()

            log_extra = {
                "event": LogEvent.RECONCILE_COMPLETE,
                "reconcile_id": reconcile_id,
                "processed": processed_count,
                "changed": changed_count,
                "actions": dict(action_counts) if action_counts else {},
                "duration_ms": duration_ms,
                "load_ms": load_ms,
                "plan_ms": plan_ms,
                "exec_ms": exec_ms,
                "persist_ms": persist_ms,
            }

            # 1시간마다 heartbeat (변화 없어도 "살아있음" 확인)
            if now - self._last_heartbeat >= 3600:
                logger.info("Heartbeat", extra=log_extra)
                self._last_heartbeat = now
                self._prev_state = current_state
            elif current_state != self._prev_state:
                logger.info("Reconcile completed", extra=log_extra)
                self._prev_state = current_state

            # Slow reconcile warning (SLO threat detection)
            if duration_ms > _logging_config.slow_threshold_ms:
                logger.warning(
                    "Slow reconcile detected",
                    extra={
                        "event": LogEvent.RECONCILE_SLOW,
                        "reconcile_id": reconcile_id,
                        "duration_ms": duration_ms,
                        "threshold_ms": _logging_config.slow_threshold_ms,
                        "processed": processed_count,
                        "load_ms": load_ms,
                        "plan_ms": plan_ms,
                        "exec_ms": exec_ms,
                        "persist_ms": persist_ms,
                    },
                )
        finally:
            clear_trace_context()

    def _judge_and_plan(self, ws: Workspace) -> PlanAction:
        """Judge + Plan (순수 계산, DB 미사용).

        wc_planner.plan()에 위임합니다.
        """
        plan_input = PlanInput.from_workspace(ws)
        return plan(plan_input, timeout_seconds=self.OPERATION_TIMEOUT)

    def _needs_execute(self, action: PlanAction, ws: Workspace) -> bool:
        """Execute 필요 여부 판단.

        wc_planner.needs_execute()에 위임합니다.
        """
        return needs_execute(action, Operation(ws.operation))

    async def _execute_one(
        self, ws: Workspace, action: PlanAction
    ) -> tuple[Workspace, PlanAction]:
        """Execute single workspace operation with retry and error handling.

        Early return 패턴으로 실행 불필요 시 즉시 리턴합니다.
        """
        if not self._needs_execute(action, ws):
            return (ws, action)

        try:
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
        except asyncio.TimeoutError:
            logger.error(
                "Operation timeout",
                extra={
                    "event": LogEvent.OPERATION_TIMEOUT,
                    "ws_id": ws.id,
                    "operation": action.operation.value,
                    "error_class": "timeout",
                    "timeout_s": self.OPERATION_TIMEOUT,
                },
            )
        except Exception as exc:
            error_class = classify_error(exc)
            logger.exception(
                "Operation failed",
                extra={
                    "event": LogEvent.OPERATION_FAILED,
                    "ws_id": ws.id,
                    "operation": action.operation.value,
                    "error_class": error_class,
                    "retryable": error_class == "transient",
                },
            )
        return (ws, action)

    async def _execute(self, ws: Workspace, action: PlanAction) -> None:
        """Actuator 호출.

        계약 #8: 순서 보장
        - ARCHIVING: archive() → delete_volume()
        - DELETING: delete() → delete_volume()
        """
        start = time.perf_counter()
        try:
            match action.operation:
                case Operation.PROVISIONING:
                    await self._sp.provision(ws.id)

                case Operation.RESTORING:
                    if ws.archive_key:
                        await self._sp.restore(ws.id, ws.archive_key)
                        action.restore_marker = ws.archive_key  # 완료 확인용 marker

                case Operation.STARTING:
                    await self._ic.start(ws.id, ws.image_ref)

                case Operation.STOPPING:
                    await self._ic.delete(ws.id)

                case Operation.ARCHIVING:
                    # 3단계 operation: archive → delete container → delete_volume
                    # 컨테이너 삭제 추가: Exited 컨테이너도 볼륨 참조하므로 먼저 삭제 필요
                    op_id = action.op_id or ws.op_id or str(uuid4())
                    archive_key = await self._sp.archive(ws.id, op_id)
                    action.archive_key = archive_key
                    await self._ic.delete(ws.id)  # Exited 컨테이너 정리 (idempotent)
                    await self._sp.delete_volume(ws.id)

                case Operation.CREATE_EMPTY_ARCHIVE:
                    op_id = action.op_id or ws.op_id or str(uuid4())
                    archive_key = await self._sp.create_empty_archive(ws.id, op_id)
                    action.archive_key = archive_key

                case Operation.DELETING:
                    # 2단계 operation: delete container → delete_volume (계약 #8)
                    await self._ic.delete(ws.id)
                    await self._sp.delete_volume(ws.id)
        finally:
            duration = time.perf_counter() - start
            WC_OPERATION_DURATION.labels(operation=action.operation.name).observe(duration)

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
            WC_CAS_FAILURES_TOTAL.inc()
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
