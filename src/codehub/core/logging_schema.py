"""Logging field schema - v1.0

Standard fields (added to all logs):
- schema_version: Log schema version
- service: Service name (codehub-control-plane)
- component: Component name (WC, OB, TTL, GC, API)
- event: Event type (reconcile_complete, operation_failed, etc.)
- trace_id: Distributed trace ID (W3C traceparent)
- duration_ms: Duration in milliseconds

High cardinality fields (OK in logs, NOT in metric labels):
- ws_id: Workspace ID
- user_id: User ID
- request_id: Request ID
"""

from enum import StrEnum


class LogEvent(StrEnum):
    """Standard log event types.

    Use these event types in the 'event' extra field for consistent
    log filtering and analysis.
    """

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
    """Error classification for structured error logging.

    Use these in the 'error_class' extra field to enable
    filtering by error type and setting up alerts.
    """

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
