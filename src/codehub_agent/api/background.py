"""Background task management for API endpoints.

Provides utilities for executing fire-and-forget background tasks
with proper error handling and logging.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)


async def safe_background_task(
    coro: Coroutine[Any, Any, Any],
    context: dict[str, Any],
) -> None:
    """Execute background task with exception logging.

    This function wraps a coroutine to catch and log any exceptions,
    preventing them from being silently swallowed when running as
    a background task.

    Args:
        coro: The coroutine to execute.
        context: Context dictionary for logging (e.g., workspace_id, operation).
    """
    try:
        await coro
    except Exception:
        logger.exception(
            "Background task failed",
            extra={
                "event": "BACKGROUND_TASK_FAILED",
                **context,
            },
        )


def spawn_background_task(
    coro: Coroutine[Any, Any, Any],
    context: dict[str, Any],
) -> asyncio.Task:
    """Spawn a fire-and-forget background task.

    Creates an asyncio task that executes the coroutine with proper
    error handling. The task is not awaited - it runs in the background.

    Args:
        coro: The coroutine to execute.
        context: Context dictionary for logging.

    Returns:
        The created asyncio Task.
    """
    return asyncio.create_task(safe_background_task(coro, context))
