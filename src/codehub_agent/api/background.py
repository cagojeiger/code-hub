"""Background task utilities."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)


async def safe_background_task(
    coro: Coroutine[Any, Any, Any],
    context: dict[str, Any],
) -> None:
    """Execute coroutine with exception logging."""
    try:
        await coro
    except Exception:
        logger.exception(
            "Background task failed",
            extra={"event": "BACKGROUND_TASK_FAILED", **context},
        )


def spawn_background_task(
    coro: Coroutine[Any, Any, Any],
    context: dict[str, Any],
) -> asyncio.Task:
    """Spawn fire-and-forget background task."""
    return asyncio.create_task(safe_background_task(coro, context))
