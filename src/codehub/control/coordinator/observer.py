"""Observer Coordinator - 리소스 관측 → conditions DB 저장.

Algorithm:
1. WorkspaceRuntime.observe() 호출 (Agent가 3개 리소스 통합)
2. 실패 → reconcile skip (상태 일관성 보장)
3. 성공 → DB 기준 모든 workspace 업데이트
   - 리소스 있음 → conditions에 상태 기록
   - 리소스 없음 → null로 덮어씀 (삭제 감지 위해 필수)
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.app.config import get_settings
from codehub.app.metrics.collector import (
    OBSERVER_ARCHIVES,
    OBSERVER_CONTAINERS,
    OBSERVER_OBSERVE_DURATION,
    OBSERVER_STAGE_DURATION,
    OBSERVER_VOLUMES,
    OBSERVER_WORKSPACES,
    RUNTIME_OBSERVE_DURATION,
    WORKSPACES_BY_STATE,
)
from codehub.control.coordinator.base import (
    ChannelSubscriber,
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
)
from codehub.core.interfaces.runtime import WorkspaceRuntime, WorkspaceState
from codehub.core.logging_schema import LogEvent
from codehub.core.models import Workspace

logger = logging.getLogger(__name__)
_settings = get_settings()
_logging_config = _settings.logging


class ObserverCoordinator(CoordinatorBase):
    """Observer - conditions, observed_at 컬럼 소유."""

    COORDINATOR_TYPE = CoordinatorType.OBSERVER
    WAKE_TARGET = "observer"

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        subscriber: ChannelSubscriber,
        runtime: WorkspaceRuntime,
    ) -> None:
        super().__init__(conn, leader, subscriber)
        self._runtime = runtime
        self._timeout_s = _settings.observer.timeout_s
        # Track previous state to log only on changes (reduces noise)
        self._prev_state: tuple[int, int, int, int] | None = None
        self._last_heartbeat: float = 0.0
        # Track previous container IDs to detect disappeared containers
        self._prev_container_ids: set[str] | None = None
        # Track previous conditions to skip unchanged updates (reduces DB I/O)
        self._prev_conditions: dict[str, dict] | None = None

    async def _observe_with_timeout(self) -> list[WorkspaceState] | None:
        """Call runtime.observe() with timeout."""
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._runtime.observe(), timeout=self._timeout_s
            )
            duration = time.monotonic() - start
            OBSERVER_OBSERVE_DURATION.observe(duration)  # Legacy
            RUNTIME_OBSERVE_DURATION.observe(duration)   # New
            return result
        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            OBSERVER_OBSERVE_DURATION.observe(duration)  # Legacy
            RUNTIME_OBSERVE_DURATION.observe(duration)   # New
            logger.warning(
                "Observe timeout",
                extra={
                    "event": LogEvent.OPERATION_TIMEOUT,
                    "operation": "observe",
                    "timeout_s": self._timeout_s,
                },
            )
            return None
        except Exception as exc:
            duration = time.monotonic() - start
            OBSERVER_OBSERVE_DURATION.observe(duration)  # Legacy
            RUNTIME_OBSERVE_DURATION.observe(duration)   # New
            logger.exception(
                "Observe failed",
                extra={
                    "event": LogEvent.OPERATION_FAILED,
                    "operation": "observe",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return None

    async def reconcile(self) -> None:
        reconcile_start = time.monotonic()

        # Stage 1: Load workspace IDs from DB
        load_start = time.monotonic()
        ws_ids = await self._load_workspace_ids()
        OBSERVER_STAGE_DURATION.labels(stage="load").observe(time.monotonic() - load_start)
        if not ws_ids:
            return

        # Stage 2: Observe resources via WorkspaceRuntime (single API call)
        workspace_states = await self._observe_with_timeout()
        if workspace_states is None:
            logger.warning(
                "Observation failed, skipping reconcile",
                extra={"event": LogEvent.OPERATION_FAILED},
            )
            return

        # Index workspace states by workspace_id
        states_map: dict[str, WorkspaceState] = {
            ws.workspace_id: ws for ws in workspace_states
        }

        # Orphan 경고 (DB에 없는데 리소스 있음 → GC 대상)
        observed_ws_ids = set(states_map.keys())
        for ws_id in observed_ws_ids - ws_ids:
            logger.warning(
                "Orphan detected",
                extra={"event": LogEvent.CONTAINER_DISAPPEARED, "ws_id": ws_id},
            )

        # Detect disappeared containers (critical for OOM/crash diagnosis)
        current_container_ids = {
            ws_id for ws_id, state in states_map.items()
            if state.container and state.container.running
        }
        if self._prev_container_ids is not None:
            disappeared = self._prev_container_ids - current_container_ids
            for ws_id in disappeared:
                # Only warn if the workspace still exists (not deleted)
                if ws_id in ws_ids:
                    logger.warning(
                        "Container disappeared",
                        extra={
                            "event": LogEvent.CONTAINER_DISAPPEARED,
                            "ws_id": ws_id,
                        },
                    )
        self._prev_container_ids = current_container_ids

        # Count resources for metrics (legacy)
        container_count = sum(
            1 for state in workspace_states if state.container is not None
        )
        volume_count = sum(
            1 for state in workspace_states if state.volume is not None
        )
        archive_count = sum(
            1 for state in workspace_states if state.archive is not None
        )

        # Calculate workspace states for new metrics
        state_counts = {
            "running": 0,
            "unhealthy": 0,
            "stopped": 0,
            "archived": 0,
            "provisioning": 0,
            "unknown": 0,
        }
        for state in workspace_states:
            ws_state = self._determine_workspace_state(state)
            state_counts[ws_state] += 1

        # Stage 3: Bulk update conditions in DB
        update_start = time.monotonic()
        count = await self._bulk_update_conditions_v2(ws_ids, states_map)
        await self._conn.commit()
        OBSERVER_STAGE_DURATION.labels(stage="update").observe(time.monotonic() - update_start)

        # Update legacy metrics (deprecated)
        OBSERVER_WORKSPACES.set(count)
        OBSERVER_CONTAINERS.set(container_count)
        OBSERVER_VOLUMES.set(volume_count)
        OBSERVER_ARCHIVES.set(archive_count)

        # Update new workspace-centric metrics
        for state_name, state_count in state_counts.items():
            WORKSPACES_BY_STATE.labels(state=state_name).set(state_count)

        duration_ms = (time.monotonic() - reconcile_start) * 1000

        # Log only when state changes OR 1-hour heartbeat (reduces noise from ~86k/day)
        current_state = (count, container_count, volume_count, archive_count)
        now = time.monotonic()

        # 1시간마다 heartbeat (변화 없어도 "살아있음" 확인)
        if now - self._last_heartbeat >= 3600:
            logger.info(
                "Heartbeat",
                extra={
                    "event": LogEvent.OBSERVATION_COMPLETE,
                    "workspaces": count,
                    "containers": container_count,
                    "volumes": volume_count,
                    "archives": archive_count,
                    "duration_ms": duration_ms,
                },
            )
            self._last_heartbeat = now
            self._prev_state = current_state
        elif current_state != self._prev_state:
            logger.info(
                "Observation completed",
                extra={
                    "event": LogEvent.OBSERVATION_COMPLETE,
                    "workspaces": count,
                    "containers": container_count,
                    "volumes": volume_count,
                    "archives": archive_count,
                    "duration_ms": duration_ms,
                },
            )
            self._prev_state = current_state

        # Slow observation warning (SLO threat detection)
        if duration_ms > _logging_config.slow_threshold_ms:
            logger.warning(
                "Slow observation detected",
                extra={
                    "event": LogEvent.RECONCILE_SLOW,
                    "duration_ms": duration_ms,
                    "threshold_ms": _logging_config.slow_threshold_ms,
                    "workspaces": count,
                },
            )

    async def _load_workspace_ids(self) -> set[str]:
        result = await self._conn.execute(
            select(Workspace.id).where(Workspace.deleted_at.is_(None))
        )
        return {str(row[0]) for row in result.fetchall()}

    async def _bulk_update_conditions_v2(
        self,
        ws_ids: set[str],
        states_map: dict[str, WorkspaceState],
    ) -> int:
        """O(1) round-trip bulk UPDATE using WorkspaceState.

        Optimization: Only updates workspaces with changed conditions.
        Reduces DB I/O by 70-90% when workspace state is stable.
        """
        now = datetime.now(UTC)

        # Build current conditions map
        current_conditions: dict[str, dict] = {}
        for ws_id in ws_ids:
            state = states_map.get(ws_id)
            if state:
                current_conditions[ws_id] = {
                    "container": (
                        {"running": state.container.running, "healthy": state.container.healthy}
                        if state.container else None
                    ),
                    "volume": (
                        {"exists": state.volume.exists}
                        if state.volume else None
                    ),
                    "archive": (
                        {"exists": state.archive.exists, "archive_key": state.archive.archive_key}
                        if state.archive else None
                    ),
                    "restore": (
                        {"restore_op_id": state.restore.restore_op_id, "archive_key": state.restore.archive_key}
                        if state.restore else None
                    ),
                }
            else:
                current_conditions[ws_id] = {
                    "container": None,
                    "volume": None,
                    "archive": None,
                    "restore": None,
                }

        # Detect changed workspaces only
        changed_ws_ids: list[str] = []
        changed_conditions: list[dict] = []

        for ws_id in ws_ids:
            curr = current_conditions[ws_id]
            prev = self._prev_conditions.get(ws_id) if self._prev_conditions else None
            if prev != curr:
                changed_ws_ids.append(ws_id)
                changed_conditions.append(curr)

        # Update cache with current conditions
        self._prev_conditions = current_conditions

        # Skip DB update if no changes
        if not changed_ws_ids:
            logger.debug(
                "No condition changes, skipping UPDATE",
                extra={"event": LogEvent.OBSERVATION_COMPLETE, "total_ws": len(ws_ids)},
            )
            return len(ws_ids)

        # Only update changed workspaces
        result = await self._conn.execute(
            text("""
                UPDATE workspaces AS w
                SET conditions = v.cond::jsonb, observed_at = v.ts
                FROM unnest(
                    CAST(:ids AS text[]),
                    CAST(:conds AS jsonb[]),
                    CAST(:timestamps AS timestamptz[])
                ) AS v(id, cond, ts)
                WHERE w.id = v.id
                RETURNING w.id
            """),
            {
                "ids": changed_ws_ids,
                "conds": [json.dumps(c) for c in changed_conditions],
                "timestamps": [now] * len(changed_ws_ids),
            },
        )

        updated_count = len(result.fetchall())
        if updated_count > 0:
            logger.debug(
                "Conditions updated",
                extra={
                    "event": LogEvent.OBSERVATION_COMPLETE,
                    "total_ws": len(ws_ids),
                    "changed_ws": updated_count,
                },
            )
        return len(ws_ids)

    def _determine_workspace_state(self, state: WorkspaceState) -> str:
        """Determine workspace state from WorkspaceState for metrics.

        States:
          - running: container running and healthy
          - unhealthy: container running but not healthy
          - stopped: container not running, volume exists
          - archived: no container, no volume, archive exists
          - provisioning: volume exists, no container
          - unknown: no resources detected
        """
        has_container = state.container is not None
        has_volume = state.volume is not None and state.volume.exists
        has_archive = state.archive is not None and state.archive.exists

        if has_container:
            if state.container.running:
                if state.container.healthy:
                    return "running"
                return "unhealthy"
            # Container exists but not running
            if has_volume:
                return "stopped"

        if has_volume:
            # Volume exists but no container
            return "provisioning"

        if has_archive:
            return "archived"

        return "unknown"
