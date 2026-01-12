"""Observer Coordinator - 리소스 관측 → conditions DB 저장.

Algorithm:
1. 3개 API (containers, volumes, archives) 병렬 호출 with timeout
2. 하나라도 실패 → reconcile skip (상태 일관성 보장)
3. 전체 성공 → DB 기준 모든 workspace 업데이트
   - 리소스 있음 → conditions에 상태 기록
   - 리소스 없음 → null로 덮어씀 (삭제 감지 위해 필수)
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from typing import Coroutine

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.app.config import get_settings
from codehub.app.metrics.collector import (
    OBSERVER_API_DURATION,
    OBSERVER_ARCHIVES,
    OBSERVER_CONTAINERS,
    OBSERVER_STAGE_DURATION,
    OBSERVER_VOLUMES,
    OBSERVER_WORKSPACES,
)
from codehub.control.coordinator.base import (
    ChannelSubscriber,
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
)
from codehub.core.interfaces.instance import ContainerInfo, InstanceController
from codehub.core.interfaces.storage import ArchiveInfo, StorageProvider, VolumeInfo
from codehub.core.logging_schema import LogEvent
from codehub.core.models import Workspace

logger = logging.getLogger(__name__)
_settings = get_settings()
_logging_config = _settings.logging


class BulkObserver:
    """3개 API 병렬 호출로 리소스 관측."""

    def __init__(self, ic: InstanceController, sp: StorageProvider) -> None:
        self._ic = ic
        self._sp = sp
        self._prefix = _settings.runtime.resource_prefix
        self._timeout_s = _settings.observer.timeout_s

    async def _safe[T](self, coro: Coroutine[None, None, list[T]], name: str) -> list[T] | None:
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(coro, timeout=self._timeout_s)
            OBSERVER_API_DURATION.labels(api=name).observe(time.monotonic() - start)
            return result
        except asyncio.TimeoutError:
            OBSERVER_API_DURATION.labels(api=name).observe(time.monotonic() - start)
            logger.warning(
                "Operation timeout",
                extra={
                    "event": LogEvent.OPERATION_TIMEOUT,
                    "operation": name,
                    "timeout_s": self._timeout_s,
                },
            )
            return None
        except Exception as exc:
            OBSERVER_API_DURATION.labels(api=name).observe(time.monotonic() - start)
            logger.exception(
                "Operation failed",
                extra={
                    "event": LogEvent.OPERATION_FAILED,
                    "operation": name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return None

    async def observe_all(self) -> tuple[
        dict[str, ContainerInfo] | None,
        dict[str, VolumeInfo] | None,
        dict[str, ArchiveInfo] | None,
    ]:
        results = await asyncio.gather(
            self._safe(self._ic.list_all(self._prefix), "containers"),
            self._safe(self._sp.list_volumes(self._prefix), "volumes"),
            self._safe(self._sp.list_archives(self._prefix), "archives"),
        )

        c_list, v_list, a_list = results
        containers = {c.workspace_id: c for c in c_list} if c_list is not None else None
        volumes = {v.workspace_id: v for v in v_list} if v_list is not None else None
        archives = {a.workspace_id: a for a in a_list} if a_list is not None else None

        return containers, volumes, archives


class ObserverCoordinator(CoordinatorBase):
    """Observer - conditions, observed_at 컬럼 소유."""

    COORDINATOR_TYPE = CoordinatorType.OBSERVER
    WAKE_TARGET = "observer"

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        subscriber: ChannelSubscriber,
        ic: InstanceController,
        sp: StorageProvider,
    ) -> None:
        super().__init__(conn, leader, subscriber)
        self._observer = BulkObserver(ic, sp)
        # Track previous state to log only on changes (reduces noise)
        self._prev_state: tuple[int, int, int, int] | None = None
        self._last_heartbeat: float = 0.0
        # Track previous container IDs to detect disappeared containers
        self._prev_container_ids: set[str] | None = None

    async def reconcile(self) -> None:
        reconcile_start = time.monotonic()

        # Stage 1: Load workspace IDs from DB
        load_start = time.monotonic()
        ws_ids = await self._load_workspace_ids()
        OBSERVER_STAGE_DURATION.labels(stage="load").observe(time.monotonic() - load_start)
        if not ws_ids:
            return

        # Stage 2: Observe resources (parallel API calls)
        observe_start = time.monotonic()
        containers, volumes, archives = await self._observer.observe_all()
        OBSERVER_STAGE_DURATION.labels(stage="observe").observe(time.monotonic() - observe_start)

        # 하나라도 실패 → skip (상태 일관성 보장, 다음 reconcile에서 재시도)
        if any(x is None for x in [containers, volumes, archives]):
            logger.warning(
                "Observation failed, skipping reconcile",
                extra={"event": LogEvent.OPERATION_FAILED},
            )
            return

        # Orphan 경고 (DB에 없는데 리소스 있음 → GC 대상)
        observed_ws_ids = set(containers) | set(volumes) | set(archives)
        for ws_id in observed_ws_ids - ws_ids:
            logger.warning(
                "Orphan detected",
                extra={"event": LogEvent.CONTAINER_DISAPPEARED, "ws_id": ws_id},
            )

        # Detect disappeared containers (critical for OOM/crash diagnosis)
        current_container_ids = set(containers.keys())
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

        # Stage 3: Bulk update conditions in DB
        update_start = time.monotonic()
        count = await self._bulk_update_conditions(ws_ids, containers, volumes, archives)
        await self._conn.commit()
        OBSERVER_STAGE_DURATION.labels(stage="update").observe(time.monotonic() - update_start)

        # Update metrics
        OBSERVER_WORKSPACES.set(count)
        OBSERVER_CONTAINERS.set(len(containers))
        OBSERVER_VOLUMES.set(len(volumes))
        OBSERVER_ARCHIVES.set(len(archives))

        duration_ms = (time.monotonic() - reconcile_start) * 1000

        # Log only when state changes OR 1-hour heartbeat (reduces noise from ~86k/day)
        current_state = (count, len(containers), len(volumes), len(archives))
        now = time.monotonic()

        # 1시간마다 heartbeat (변화 없어도 "살아있음" 확인)
        if now - self._last_heartbeat >= 3600:
            logger.info(
                "Heartbeat",
                extra={
                    "event": LogEvent.OBSERVATION_COMPLETE,
                    "workspaces": count,
                    "containers": len(containers),
                    "volumes": len(volumes),
                    "archives": len(archives),
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
                    "containers": len(containers),
                    "volumes": len(volumes),
                    "archives": len(archives),
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

    async def _bulk_update_conditions(
        self,
        ws_ids: set[str],
        containers: dict[str, ContainerInfo],
        volumes: dict[str, VolumeInfo],
        archives: dict[str, ArchiveInfo],
    ) -> int:
        """O(1) round-trip bulk UPDATE."""
        now = datetime.now(UTC)
        ws_id_list = list(ws_ids)

        conditions_list = []
        for ws_id in ws_id_list:
            c, v, a = containers.get(ws_id), volumes.get(ws_id), archives.get(ws_id)
            conditions_list.append({
                "container": c.model_dump() if c else None,
                "volume": v.model_dump() if v else None,
                "archive": a.model_dump() if a else None,
            })

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
                "ids": ws_id_list,
                "conds": [json.dumps(c) for c in conditions_list],
                "timestamps": [now] * len(ws_id_list),
            },
        )
        return len(result.fetchall())
