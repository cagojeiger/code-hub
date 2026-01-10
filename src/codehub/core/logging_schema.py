"""Log event and error classification enums.

See docs/LOGGING.md for detailed usage guide.
"""

from enum import StrEnum


class LogEvent(StrEnum):
    """Log event types. See docs/LOGGING.md for usage."""

    # Coordinator events
    RECONCILE_COMPLETE = "reconcile_complete"
    RECONCILE_SLOW = "reconcile_slow"
    OBSERVATION_COMPLETE = "observation_complete"
    STATE_CHANGED = "state_changed"
    OPERATION_FAILED = "operation_failed"
    OPERATION_TIMEOUT = "operation_timeout"
    OPERATION_SUCCESS = "operation_success"

    # Leadership events
    LEADERSHIP_ACQUIRED = "leadership_acquired"
    LEADERSHIP_LOST = "leadership_lost"

    # Resource events
    CONTAINER_DISAPPEARED = "container_disappeared"

    # Container lifecycle events
    CONTAINER_STARTED = "container_started"
    CONTAINER_STOPPED = "container_stopped"
    CONTAINER_EXITED = "container_exited"

    # Volume lifecycle events
    VOLUME_CREATED = "volume_created"
    VOLUME_REMOVED = "volume_removed"

    # Archive/Restore events
    ARCHIVE_SUCCESS = "archive_success"
    ARCHIVE_FAILED = "archive_failed"
    RESTORE_SUCCESS = "restore_success"
    RESTORE_FAILED = "restore_failed"

    # Lifecycle events
    APP_STARTED = "app_started"
    APP_STOPPED = "app_stopped"

    # API events
    REQUEST_COMPLETE = "request_complete"
    REQUEST_FAILED = "request_failed"
    REQUEST_SLOW = "request_slow"

    # CDC/EventListener events
    NOTIFY_RECEIVED = "notify_received"
    WAKE_PUBLISHED = "wake_published"
    SSE_PUBLISHED = "sse_published"

    # WebSocket/Proxy events
    WS_ERROR = "ws_error"
    UPSTREAM_ERROR = "upstream_error"

    # SSE events
    SSE_CONNECTED = "sse_connected"
    SSE_DISCONNECTED = "sse_disconnected"
    SSE_RECEIVED = "sse_received"

    # Infrastructure events
    DB_CONNECTED = "db_connected"
    DB_ERROR = "db_error"
    S3_CONNECTED = "s3_connected"
    S3_BUCKET_CREATED = "s3_bucket_created"
    S3_ERROR = "s3_error"
    REDIS_SUBSCRIBED = "redis_subscribed"
    REDIS_CONNECTION_ERROR = "redis_connection_error"


class ErrorClass(StrEnum):
    """Error classification. See docs/LOGGING.md for usage."""

    TRANSIENT = "transient"  # Retryable (network timeout, temp failure)
    PERMANENT = "permanent"  # Not retryable (invalid input, not found)
    TIMEOUT = "timeout"  # Timeout error
    RATE_LIMITED = "rate_limited"  # Rate limit exceeded


class Component(StrEnum):
    """Component identifiers for log filtering."""

    WC = "wc"  # WorkspaceController
    OB = "ob"  # Observer
    TTL = "ttl"  # TTL Coordinator
    GC = "gc"  # Garbage Collector
    API = "api"  # REST API
    SSE = "sse"  # Server-Sent Events
    CDC = "cdc"  # Change Data Capture (EventListener)
