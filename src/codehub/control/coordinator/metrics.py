"""Metrics Collector Coordinator - Gauge metrics update."""

import logging

from sqlalchemy import func, select

from codehub.app.metrics.collector import (
    DB_POOL_CHECKEDIN,
    DB_POOL_CHECKEDOUT,
    DB_POOL_OVERFLOW,
    DB_UP,
    WORKSPACE_COUNT_BY_OPERATION,
    WORKSPACE_COUNT_BY_STATE,
)
from codehub.control.coordinator.base import CoordinatorBase, CoordinatorType
from codehub.core.domain.workspace import Operation, Phase
from codehub.core.models import Workspace
from codehub.infra import get_engine

logger = logging.getLogger(__name__)


class MetricsCollector(CoordinatorBase):
    """Metrics collection coordinator.

    리더만 Gauge 메트릭을 주기적으로 업데이트합니다.
    - workspace count by phase
    - workspace count by operation
    - DB pool stats

    Follower는 아무 작업도 하지 않으므로 DB 쿼리 부하가 1/N로 감소합니다.
    """

    COORDINATOR_TYPE = CoordinatorType.METRICS
    WAKE_TARGET = "metrics"

    async def tick(self) -> None:
        """Collect and update Gauge metrics."""
        await self._update_workspace_count_metrics()
        self._update_db_pool_metrics()

    async def _update_workspace_count_metrics(self) -> None:
        """Update workspace count gauges."""
        try:
            # Count by phase
            stmt = (
                select(Workspace.phase, func.count(Workspace.id))
                .where(Workspace.deleted_at.is_(None))
                .group_by(Workspace.phase)
            )
            result = await self._conn.execute(stmt)

            # Reset all to 0 first (handle phases with no workspaces)
            for phase in Phase:
                # Exclude DELETED/DELETING from metrics (not relevant for monitoring)
                if phase not in (Phase.DELETED, Phase.DELETING):
                    WORKSPACE_COUNT_BY_STATE.labels(phase=phase.name).set(0)

            for phase_value, count in result.fetchall():
                phase = Phase(phase_value)
                if phase not in (Phase.DELETED, Phase.DELETING):
                    WORKSPACE_COUNT_BY_STATE.labels(phase=phase.name).set(count)

            # Count by operation
            stmt = (
                select(Workspace.operation, func.count(Workspace.id))
                .where(
                    Workspace.deleted_at.is_(None),
                    Workspace.operation != Operation.NONE.value,
                )
                .group_by(Workspace.operation)
            )
            result = await self._conn.execute(stmt)

            # Reset all to 0
            for op in Operation:
                if op != Operation.NONE:
                    WORKSPACE_COUNT_BY_OPERATION.labels(operation=op.name).set(0)

            for operation_value, count in result.fetchall():
                WORKSPACE_COUNT_BY_OPERATION.labels(
                    operation=Operation(operation_value).name
                ).set(count)

            logger.debug("[%s] Updated workspace count metrics", self.name)
        except Exception as e:
            logger.warning("[%s] Failed to update workspace count metrics: %s", self.name, e)

    def _update_db_pool_metrics(self) -> None:
        """Update database pool metrics."""
        try:
            engine = get_engine()
            pool = engine.pool
            DB_UP.set(1)
            DB_POOL_CHECKEDIN.set(pool.checkedin())
            DB_POOL_CHECKEDOUT.set(pool.checkedout())
            # overflow() can return negative values when no overflow connections exist
            DB_POOL_OVERFLOW.set(max(0, pool.overflow()))
        except Exception as e:
            logger.warning("[%s] Failed to update DB pool metrics: %s", self.name, e)
            DB_UP.set(0)
