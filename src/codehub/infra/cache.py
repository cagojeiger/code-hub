"""TTL cache utilities for proxy auth.

Reduces DB load during page loads (10-50 requests in 500ms).
Configuration via CacheConfig (CACHE_ env prefix).
"""

from typing import TYPE_CHECKING

from cachetools import TTLCache

from codehub.app.config import get_settings

if TYPE_CHECKING:
    from codehub.core.models import Workspace

_cache_config = get_settings().cache

session_cache: TTLCache[str, str] = TTLCache(
    maxsize=_cache_config.maxsize, ttl=_cache_config.ttl
)
workspace_cache: TTLCache[tuple[str, str], "Workspace"] = TTLCache(
    maxsize=_cache_config.maxsize, ttl=_cache_config.ttl
)


def clear_session_cache(session_id: str | None = None) -> None:
    if session_id is None:
        session_cache.clear()
    else:
        session_cache.pop(session_id, None)


def clear_workspace_cache(
    workspace_id: str | None = None, user_id: str | None = None
) -> None:
    if workspace_id is None:
        workspace_cache.clear()
    elif user_id is not None:
        workspace_cache.pop((workspace_id, user_id), None)
    else:
        keys_to_remove = [k for k in workspace_cache if k[0] == workspace_id]
        for key in keys_to_remove:
            del workspace_cache[key]


def clear_all_caches() -> None:
    session_cache.clear()
    workspace_cache.clear()
