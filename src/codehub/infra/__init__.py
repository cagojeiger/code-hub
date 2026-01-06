"""Infrastructure connections (DB, Redis, S3, Docker, Cache)."""

from codehub.core.models import Workspace
from codehub.infra.cache import (
    clear_all_caches,
    clear_session_cache,
    clear_workspace_cache,
    session_cache,
    workspace_cache,
)
from codehub.infra.docker import close_docker, get_docker_client
from codehub.infra.s3 import close_storage, get_s3_client, init_storage
from codehub.infra.postgresql import (
    close_db,
    get_engine,
    get_session,
    get_session_factory,
    init_db,
)
from codehub.infra.redis import close_redis, get_redis, init_redis
from codehub.infra.redis_kv import ActivityStore, get_activity_store
from codehub.infra.redis_pubsub import ChannelPublisher, ChannelSubscriber

__all__ = [
    # Cache
    "session_cache",
    "workspace_cache",
    "clear_session_cache",
    "clear_workspace_cache",
    "clear_all_caches",
    # DB
    "init_db",
    "close_db",
    "get_engine",
    "get_session",
    "get_session_factory",
    # Models
    "Workspace",
    # Redis - client
    "init_redis",
    "close_redis",
    "get_redis",
    "get_activity_store",
    # Redis - classes
    "ChannelPublisher",
    "ChannelSubscriber",
    "ActivityStore",
    # Storage
    "init_storage",
    "close_storage",
    "get_s3_client",
    # Docker
    "close_docker",
    "get_docker_client",
]
