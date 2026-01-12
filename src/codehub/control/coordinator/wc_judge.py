"""Phase calculation and invariant checking.

Reference: docs/architecture_v2/wc-judge.md
"""

from pydantic import BaseModel

from codehub.core.domain.conditions import ConditionInput
from codehub.core.domain.workspace import (
    ErrorReason,
    Phase,
)


class JudgeInput(BaseModel):
    """Judge 함수 입력.

    Attributes:
        conditions: 관측된 condition 요약
        deleted_at: 삭제 요청 여부
    """

    conditions: ConditionInput
    deleted_at: bool

    model_config = {"frozen": True}


class JudgeOutput(BaseModel):
    """Judge 함수 출력.

    Attributes:
        phase: 계산된 Phase
        healthy: 불변식 준수 여부
        error_reason: 불변식 위반 시 에러 사유
    """

    phase: Phase
    healthy: bool
    error_reason: ErrorReason | None = None

    model_config = {"frozen": True}


def check_invariants(conditions: ConditionInput) -> tuple[bool, ErrorReason | None]:
    """Check invariants and return (healthy, error_reason).

    Invariant violations:
    1. Container without Volume (계약 #6)

    Returns:
        (True, None) if healthy
        (False, ErrorReason) if invariant violated
    """
    # 계약 #6: Container without Volume
    if conditions.container_ready and not conditions.volume_ready:
        return False, ErrorReason.CONTAINER_WITHOUT_VOLUME

    return True, None


def judge(input: JudgeInput) -> JudgeOutput:
    """Calculate phase from conditions.

    Priority order (wc-judge.md):
    1. deleted_at (사용자 의도)
    2. policy.healthy (시스템 판단)
    3. resources (현실)
    4. default → PENDING

    Args:
        input: JudgeInput with conditions, deleted_at

    Returns:
        JudgeOutput with phase, healthy, error_reason
    """
    cond = input.conditions
    has_resources = cond.container_ready or cond.volume_ready or cond.archive_ready

    # Step 1: deleted_at (최우선)
    if input.deleted_at:
        phase = Phase.DELETING if has_resources else Phase.DELETED
        return JudgeOutput(phase=phase, healthy=True)

    # Step 2: healthy check
    healthy, error_reason = check_invariants(cond)
    if not healthy:
        return JudgeOutput(phase=Phase.ERROR, healthy=False, error_reason=error_reason)

    # Step 3: resources (현실) - 높은 레벨부터
    if cond.container_ready and cond.volume_ready:
        return JudgeOutput(phase=Phase.RUNNING, healthy=True)
    if cond.volume_ready:
        return JudgeOutput(phase=Phase.STANDBY, healthy=True)
    if cond.archive_ready:
        return JudgeOutput(phase=Phase.ARCHIVED, healthy=True)

    # Step 4: default
    return JudgeOutput(phase=Phase.PENDING, healthy=True)
