"""Workspace lock for Docker operations."""

import asyncio

_workspace_locks: dict[str, asyncio.Lock] = {}


def get_workspace_lock(workspace_id: str) -> asyncio.Lock:
    """Get or create a per-workspace lock.

    Prevents TOCTOU race condition by ensuring only one operation
    per workspace at a time.

    All workspace operations (volume, instance, job) share this lock.
    """
    if workspace_id not in _workspace_locks:
        _workspace_locks[workspace_id] = asyncio.Lock()
    return _workspace_locks[workspace_id]
