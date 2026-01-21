"""Log event types for structured logging."""

from enum import StrEnum


class LogEvent(StrEnum):
    """Log event types for Agent.

    Used with logger.info/warning/error extra dict:
        logger.info("message", extra={"event": LogEvent.CONTAINER_STARTED, ...})
    """

    # Application lifecycle
    APP_STARTED = "app_started"
    APP_STOPPED = "app_stopped"

    # Container events
    CONTAINER_CREATED = "container_created"
    CONTAINER_STARTED = "container_started"
    CONTAINER_STOPPED = "container_stopped"
    CONTAINER_REMOVED = "container_removed"
    CONTAINER_EXITED = "container_exited"

    # Volume events
    VOLUME_CREATED = "volume_created"
    VOLUME_REMOVED = "volume_removed"

    # Job events
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    JOB_CANCELLED = "job_cancelled"

    # Storage events
    IMAGE_PULLED = "image_pulled"
    ARCHIVE_DELETED = "archive_deleted"
    GC_COMPLETED = "gc_completed"

    # S3 events
    S3_BUCKET_READY = "s3_bucket_ready"
    S3_OBJECT_DELETED = "s3_object_deleted"
    S3_DELETE_FAILED = "s3_delete_failed"

    # Cleanup events
    CLEANUP_STARTED = "cleanup_started"
    CLEANUP_COMPLETED = "cleanup_completed"
    CLEANUP_FAILED = "cleanup_failed"

    # Error events
    UNHANDLED_EXCEPTION = "unhandled_exception"
    AGENT_ERROR = "agent_error"
