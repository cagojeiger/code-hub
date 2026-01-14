"""GC Runner - 고아 리소스 정리.

Archive: S3에 있지만 DB에 없는 파일 삭제
Container/Volume: 존재하지만 DB에 없는 리소스 삭제 (Agent의 runtime.delete() 사용)
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.app.config import get_settings
from codehub.core.interfaces.runtime import WorkspaceRuntime
from codehub.core.logging_schema import LogEvent
from codehub.core.retryable import with_retry

logger = logging.getLogger(__name__)

# Module-level settings cache
_settings = get_settings()


class GCRunner:
    """고아 리소스 정리."""

    def __init__(
        self,
        conn: AsyncConnection,
        runtime: WorkspaceRuntime,
    ) -> None:
        self._conn = conn
        self._runtime = runtime

    async def run(self) -> None:
        """GC 사이클 실행.

        WorkspaceRuntime을 사용하여 GC 수행:
        - observe(): 현재 리소스 상태 조회
        - run_gc(): 보호 목록 기반 archive GC
        - delete(): orphan container/volume 삭제
        """
        try:
            await self._cleanup_orphan_resources()
        except Exception as e:
            logger.exception("GC cycle failed: %s", e)
            raise

    async def _cleanup_orphan_resources(self) -> None:
        """Orphan 리소스 정리 (WorkspaceRuntime 사용)."""
        # 1. observe()로 현재 리소스 상태 조회
        try:
            workspace_states = await self._runtime.observe()
        except Exception as e:
            logger.error(
                "Failed to observe workspaces, skipping GC",
                extra={"event": LogEvent.OPERATION_FAILED, "error": str(e)},
            )
            return

        # 2. DB에서 유효한 workspace ID 조회
        valid_ws_ids = await self._get_valid_workspace_ids()

        # 3. Orphan workspace 식별 (observe에서 보이지만 DB에 없음)
        observed_ws_ids = {state.workspace_id for state in workspace_states}
        orphan_ws_ids = observed_ws_ids - valid_ws_ids

        if not orphan_ws_ids:
            logger.debug("No orphan workspaces found")
        else:
            # 4. Orphan workspace 삭제 (container + volume)
            for ws_id in orphan_ws_ids:
                logger.warning(
                    "Deleting orphan workspace",
                    extra={"event": LogEvent.OPERATION_SUCCESS, "ws_id": ws_id},
                )
                try:
                    await with_retry(
                        lambda ws_id=ws_id: self._runtime.delete(ws_id),
                        circuit_breaker="external",
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to delete orphan workspace",
                        extra={"event": LogEvent.OPERATION_FAILED, "ws_id": ws_id, "error": str(e)},
                    )

            logger.info(
                "Deleted orphan workspaces",
                extra={
                    "event": LogEvent.OPERATION_SUCCESS,
                    "count": len(orphan_ws_ids),
                },
            )

        # 5. Archive GC: DB에서 보호해야 할 archive 목록 조회 후 run_gc 호출
        protected = await self._get_protected_archives()
        if protected is not None:
            archive_keys, protected_workspaces = protected
            try:
                result = await self._runtime.run_gc(archive_keys, protected_workspaces)
                if result.deleted_count > 0:
                    logger.info(
                        "Deleted orphan archives",
                        extra={
                            "event": LogEvent.OPERATION_SUCCESS,
                            "deleted_count": result.deleted_count,
                            "deleted_keys": result.deleted_keys[:10],  # 상위 10개만 로그
                        },
                    )
                else:
                    logger.debug("No orphan archives to delete")
            except Exception as e:
                logger.error(
                    "Failed to run archive GC",
                    extra={"event": LogEvent.OPERATION_FAILED, "error": str(e)},
                )

    async def _get_protected_archives(
        self,
    ) -> tuple[list[str], list[tuple[str, str]]] | None:
        """Query DB for protected archives.

        Returns:
            (archive_keys, protected_workspaces) or None on error
            - archive_keys: archive_key column values (RESTORING target protection)
            - protected_workspaces: (ws_id, archive_op_id) tuples (ARCHIVING crash recovery)
        """
        try:
            # 1. archive_key 조회 (RESTORING 대상 보호)
            result1 = await self._conn.execute(
                text("""
                    SELECT DISTINCT archive_key
                    FROM workspaces
                    WHERE archive_key IS NOT NULL AND deleted_at IS NULL
                """)
            )
            archive_keys = [row[0] for row in result1.fetchall()]

            # 2. (ws_id, archive_op_id) 조회 (ARCHIVING crash 대비)
            result2 = await self._conn.execute(
                text("""
                    SELECT DISTINCT id::text, archive_op_id
                    FROM workspaces
                    WHERE archive_op_id IS NOT NULL AND deleted_at IS NULL
                """)
            )
            protected_workspaces = [(row[0], row[1]) for row in result2.fetchall()]

            logger.debug(
                "Found protected archives: %d keys, %d workspaces",
                len(archive_keys),
                len(protected_workspaces),
            )
            return archive_keys, protected_workspaces
        except Exception as e:
            logger.error(
                "Failed to query protected archives",
                extra={"event": LogEvent.OPERATION_FAILED, "error": str(e)},
            )
            return None

    async def _get_valid_workspace_ids(self) -> set[str]:
        """Get valid workspace IDs from DB."""
        result = await self._conn.execute(
            text("SELECT id::text FROM workspaces WHERE deleted_at IS NULL")
        )
        ws_ids = {row[0] for row in result.fetchall()}
        logger.debug("Found %d valid workspaces in DB", len(ws_ids))
        return ws_ids
