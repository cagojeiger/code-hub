"""Proxy policy decisions.

HTTP와 WebSocket의 Phase별 정책을 한 곳에서 관리.
드리프트(한쪽만 바뀌는 버그) 방지.
"""

from enum import Enum, auto

from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from codehub.app.config import get_settings
from codehub.core.domain import Phase
from codehub.core.errors import RunningLimitExceededError
from codehub.core.models import Workspace
from codehub.services.workspace_service import (
    list_running_workspaces,
    request_start,
)

from .pages import error_page, limit_exceeded_page, restoring_page, starting_page

# Settings
_limits_config = get_settings().limits


class ProxyDecision(Enum):
    """프록시 정책 결정 결과."""

    ALLOW = auto()  # 프록시 진행
    REDIRECT = auto()  # HTTP 리다이렉트 (starting/restoring/limit/error)
    WS_CLOSE = auto()  # WebSocket 연결 거부


class PolicyResult(BaseModel):
    """정책 결정 결과."""

    decision: ProxyDecision
    response: RedirectResponse | None = None  # HTTP용
    ws_close_code: int | None = None  # WS용
    ws_close_reason: str | None = None  # WS용

    model_config = {"frozen": True, "arbitrary_types_allowed": True}


async def decide_http(
    db: AsyncSession,
    workspace: Workspace,
    user_id: str,
) -> PolicyResult:
    """HTTP 프록시 정책 결정.

    Phase별 동작:
    - RUNNING: 프록시 진행 (ALLOW)
    - STANDBY: auto-wake + starting 페이지
    - ARCHIVED: auto-wake + restoring 페이지
    - 기타 (PENDING, ERROR, DELETED): error 페이지
    """
    if workspace.phase == Phase.RUNNING.value:
        return PolicyResult(decision=ProxyDecision.ALLOW)

    if workspace.phase in (Phase.STANDBY.value, Phase.ARCHIVED.value):
        # Auto-wake 시도
        try:
            await request_start(db, workspace.id, user_id)
        except RunningLimitExceededError:
            running_workspaces = await list_running_workspaces(db, user_id)
            return PolicyResult(
                decision=ProxyDecision.REDIRECT,
                response=limit_exceeded_page(
                    running_workspaces, _limits_config.max_running_per_user
                ),
            )

        # Phase에 따른 상태 페이지 반환
        if workspace.phase == Phase.STANDBY.value:
            return PolicyResult(
                decision=ProxyDecision.REDIRECT,
                response=starting_page(workspace),
            )
        return PolicyResult(
            decision=ProxyDecision.REDIRECT,
            response=restoring_page(workspace),
        )

    # PENDING, ERROR, DELETED 등
    return PolicyResult(
        decision=ProxyDecision.REDIRECT,
        response=error_page(workspace),
    )


def decide_ws(workspace: Workspace) -> PolicyResult:
    """WebSocket 프록시 정책 결정.

    Phase별 동작:
    - RUNNING: 프록시 진행 (ALLOW)
    - 기타: 연결 거부 (WS_CLOSE)

    Note: WebSocket은 HTML 페이지를 반환할 수 없으므로 auto-wake 안 함.
    """
    if workspace.phase == Phase.RUNNING.value:
        return PolicyResult(decision=ProxyDecision.ALLOW)

    return PolicyResult(
        decision=ProxyDecision.WS_CLOSE,
        ws_close_code=1008,
        ws_close_reason="Workspace not running",
    )
