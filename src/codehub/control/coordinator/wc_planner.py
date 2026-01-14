"""Workspace operation planning - 순수 함수로 operation 결정.

Reference: docs/architecture_v2/wc.md

WC의 Plan 로직을 순수 함수로 분리하여 테스트 용이성 향상.
Judge 결과를 받아 다음 operation을 결정합니다.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel

from codehub.control.coordinator.wc_judge import JudgeInput, JudgeOutput, judge
from codehub.core.domain.conditions import ConditionInput
from codehub.core.domain.workspace import (
    DesiredState,
    ErrorReason,
    Operation,
    Phase,
)

if TYPE_CHECKING:
    from codehub.core.models import Workspace


class PlanInput(BaseModel):
    """Planner 입력.

    Workspace에서 Plan에 필요한 필드만 추출.
    """

    id: str
    phase: Phase
    operation: Operation
    desired_state: DesiredState
    conditions: dict
    archive_key: str | None
    op_started_at: datetime | None
    archive_op_id: str | None  # archiving 전용 (S3 경로 생성용)
    deleted_at: datetime | None
    home_ctx: dict | None

    model_config = {"frozen": True}

    @classmethod
    def from_workspace(cls, ws: "Workspace") -> "PlanInput":
        """Workspace 모델에서 생성."""
        return cls(
            id=ws.id,
            phase=Phase(ws.phase),
            operation=Operation(ws.operation),
            desired_state=DesiredState(ws.desired_state),
            conditions=ws.conditions or {},
            archive_key=ws.archive_key,
            op_started_at=ws.op_started_at,
            archive_op_id=ws.archive_op_id,
            deleted_at=ws.deleted_at,
            home_ctx=ws.home_ctx,
        )


class PlanAction(BaseModel):
    """Plan 단계 결과."""

    operation: Operation
    phase: Phase
    error_reason: ErrorReason | None = None
    archive_key: str | None = None
    archive_op_id: str | None = None  # ARCHIVING/CREATE_EMPTY 전용 (S3 경로)
    complete: bool = False  # operation 완료 여부
    restore_marker: str | None = None  # restore 완료 확인용 marker


def plan(input: PlanInput, timeout_seconds: float = 300.0) -> PlanAction:
    """operation 결정 로직 (순수 함수).

    Cases:
    1. operation != NONE → 완료 조건 체크
    2. phase == ERROR → 대기 (또는 DELETING)
    3. phase == desired → no-op
    4. phase != desired → operation 선택

    Args:
        input: PlanInput
        timeout_seconds: operation timeout (기본 300초)

    Returns:
        PlanAction
    """
    # Judge 호출
    cond_input = ConditionInput.from_conditions(input.conditions)
    judge_input = JudgeInput(
        conditions=cond_input,
        deleted_at=input.deleted_at is not None,
    )
    judge_output = judge(judge_input)

    # Case 1: 진행 중인 operation
    if input.operation != Operation.NONE:
        return _handle_in_progress(input, judge_output, timeout_seconds)

    # Case 2: ERROR 처리
    if judge_output.phase == Phase.ERROR:
        if input.desired_state == DesiredState.DELETED:
            return PlanAction(
                operation=Operation.DELETING,
                phase=Phase.DELETING,
                # DELETING은 archive_op_id 불필요 (S3에 파일 안 만듦)
            )
        # ERROR 상태 유지 (수동 복구 필요)
        return PlanAction(
            operation=Operation.NONE,
            phase=Phase.ERROR,
            error_reason=judge_output.error_reason,
        )

    # Case 3: 이미 수렴됨
    target_phase = _phase_from_desired(input.desired_state)
    if judge_output.phase == target_phase:
        return PlanAction(
            operation=Operation.NONE,
            phase=judge_output.phase,
        )

    # Case 4: operation 선택
    operation = _select_operation(judge_output.phase, input.desired_state)
    if operation == Operation.NONE:
        return PlanAction(
            operation=Operation.NONE,
            phase=judge_output.phase,
        )

    # archive_op_id는 ARCHIVING/CREATE_EMPTY에서만 생성 (S3 경로용)
    archive_op_id = None
    if operation in (Operation.ARCHIVING, Operation.CREATE_EMPTY_ARCHIVE):
        archive_op_id = str(uuid4())

    return PlanAction(
        operation=operation,
        phase=judge_output.phase,
        archive_op_id=archive_op_id,
    )


def needs_execute(action: PlanAction, current_operation: Operation) -> bool:
    """Execute 필요 여부 판단."""
    if action.operation == Operation.NONE or action.complete:
        return False
    # 새 operation 시작 또는 재시도
    return current_operation == Operation.NONE or current_operation == action.operation


# === Private helpers ===


def _handle_in_progress(
    input: PlanInput,
    judge_output: JudgeOutput,
    timeout_seconds: float,
) -> PlanAction:
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
    # 완료 조건 체크
    complete = _check_completion(input.operation, input)

    if complete:
        # 완료 → phase 재계산, operation = NONE
        return PlanAction(
            operation=Operation.NONE,
            phase=judge_output.phase,
            complete=True,
        )

    # Timeout 체크
    if input.op_started_at and _is_timeout(input.op_started_at, timeout_seconds):
        return PlanAction(
            operation=Operation.NONE,
            phase=Phase.ERROR,
            error_reason=ErrorReason.TIMEOUT,
        )

    # 진행 중 → 재시도 (멱등)
    # ARCHIVING/CREATE_EMPTY는 archive_op_id 유지 (S3 경로 멱등성)
    archive_op_id = None
    if input.operation in (Operation.ARCHIVING, Operation.CREATE_EMPTY_ARCHIVE):
        archive_op_id = input.archive_op_id

    return PlanAction(
        operation=input.operation,
        phase=input.phase,
        archive_op_id=archive_op_id,
    )


def _check_completion(operation: Operation, input: PlanInput) -> bool:
    """operation 완료 조건 체크.

    kubelet 패턴: Desired(archive_op_id) vs Actual(archive_key) 비교로 완료 판단.
    """
    cond = ConditionInput.from_conditions(input.conditions)

    match operation:
        case Operation.PROVISIONING:
            return cond.volume_ready
        case Operation.RESTORING:
            # Dual Check: S3 restore_marker + Volume exists
            # Agent writes .restore_marker to S3 with archive_key after restore
            # Observer reads it and stores in conditions.restore
            restore = input.conditions.get("restore") or {}
            if restore.get("archive_key") == input.archive_key:
                return cond.volume_ready
            return False
        case Operation.STARTING:
            return cond.container_ready
        case Operation.STOPPING:
            return not cond.container_ready
        case Operation.ARCHIVING:
            # 1. 기본 조건: volume 삭제됨 + archive 존재
            if not (not cond.volume_ready and cond.archive_ready):
                return False
            # 2. Desired vs Actual: archive_key가 현재 archive_op_id와 일치하는지 검증
            # archive_key 형식: {prefix}{ws_id}/{archive_op_id}/home.tar.zst
            archive = input.conditions.get("archive") or {}
            actual_key = archive.get("archive_key")
            if not input.archive_op_id or not actual_key:
                return False
            return f"/{input.archive_op_id}/" in actual_key
        case Operation.CREATE_EMPTY_ARCHIVE:
            # CREATE_EMPTY도 동일 로직 적용
            if not cond.archive_ready:
                return False
            archive = input.conditions.get("archive") or {}
            actual_key = archive.get("archive_key")
            if not input.archive_op_id or not actual_key:
                return False
            return f"/{input.archive_op_id}/" in actual_key
        case Operation.DELETING:
            return not cond.container_ready and not cond.volume_ready
        case _:
            return False


def _is_timeout(op_started_at: datetime, timeout_seconds: float) -> bool:
    """operation timeout 체크."""
    elapsed = (datetime.now(UTC) - op_started_at).total_seconds()
    return elapsed > timeout_seconds


def _phase_from_desired(desired: DesiredState) -> Phase:
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


def _select_operation(current_phase: Phase, desired: DesiredState) -> Operation:
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
