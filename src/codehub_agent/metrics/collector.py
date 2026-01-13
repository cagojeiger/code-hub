"""Prometheus metrics definitions for CodeHub Agent.

Agent 메트릭은 인프라 레벨 작업을 추적합니다:
- Docker 작업 (container/volume lifecycle)
- S3 작업 (archive upload/download)

Control Plane 메트릭(codehub_workspaces)과 대비되는 Tier 2 메트릭입니다.
"""

from prometheus_client import Counter, Gauge, Histogram

# =============================================================================
# Histogram Buckets
# =============================================================================
# Docker/S3 operations are typically slow (100ms ~ 180s)
_BUCKETS_SLOW = (
    0.1, 0.2, 0.4, 0.8, 1.5,
    3, 6, 12, 24, 48,
    96, 180,
)  # 12 buckets

# S3 transfer operations can be very slow for large archives
_BUCKETS_TRANSFER = (
    0.5, 1, 2, 5, 10,
    20, 40, 80, 160, 300,
    600,
)  # 11 buckets

# =============================================================================
# Docker Operation Metrics
# =============================================================================
# Track Docker API calls latency and errors

AGENT_DOCKER_DURATION = Histogram(
    "codehub_agent_docker_duration_seconds",
    "Duration of Docker operations",
    ["operation"],  # create, start, stop, remove, volume_create, volume_remove
    buckets=_BUCKETS_SLOW,
)

AGENT_DOCKER_ERRORS = Counter(
    "codehub_agent_docker_errors_total",
    "Total Docker operation errors",
    ["operation", "error_type"],  # operation: same as above, error_type: api_error, timeout, etc.
)

# =============================================================================
# S3 Operation Metrics
# =============================================================================
# Track S3 API calls latency, transfer volume, and errors

AGENT_S3_DURATION = Histogram(
    "codehub_agent_s3_duration_seconds",
    "Duration of S3 operations",
    ["operation"],  # upload, download, delete, list
    buckets=_BUCKETS_TRANSFER,
)

AGENT_S3_BYTES = Counter(
    "codehub_agent_s3_bytes_total",
    "Total bytes transferred to/from S3",
    ["direction"],  # upload, download
)

AGENT_S3_ERRORS = Counter(
    "codehub_agent_s3_errors_total",
    "Total S3 operation errors",
    ["operation", "error_type"],
)

# =============================================================================
# Resource Count Metrics (Snapshot)
# =============================================================================
# Current resource counts - updated when observe() is called

AGENT_CONTAINERS_TOTAL = Gauge(
    "codehub_agent_containers_total",
    "Total number of managed containers",
)

AGENT_VOLUMES_TOTAL = Gauge(
    "codehub_agent_volumes_total",
    "Total number of managed volumes",
)


# =============================================================================
# Metric Initialization
# =============================================================================

def _init_metrics() -> None:
    """Initialize labeled metrics with zero values."""
    # Docker operations
    for op in ["create", "start", "stop", "remove", "inspect", "volume_create", "volume_remove", "volume_list"]:
        AGENT_DOCKER_DURATION.labels(operation=op)
        AGENT_DOCKER_ERRORS.labels(operation=op, error_type="api_error")
        AGENT_DOCKER_ERRORS.labels(operation=op, error_type="timeout")
        AGENT_DOCKER_ERRORS.labels(operation=op, error_type="not_found")

    # S3 operations
    for op in ["upload", "download", "delete", "list"]:
        AGENT_S3_DURATION.labels(operation=op)
        AGENT_S3_ERRORS.labels(operation=op, error_type="connection")
        AGENT_S3_ERRORS.labels(operation=op, error_type="timeout")
        AGENT_S3_ERRORS.labels(operation=op, error_type="not_found")

    # S3 transfer direction
    AGENT_S3_BYTES.labels(direction="upload")
    AGENT_S3_BYTES.labels(direction="download")


_init_metrics()
