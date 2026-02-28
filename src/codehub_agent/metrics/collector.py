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

_BUCKETS_FAST = (
    0.001, 0.005, 0.01, 0.025, 0.05,
    0.1, 0.25, 0.5, 1, 2.5,
    5, 10,
)  # 12 buckets - for proxy latency

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
    ["operation"],  # list, delete, delete_batch, exists, get_object
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
# Observe API Sub-call Metrics
# =============================================================================
# Track individual sub-call durations in the observe endpoint

AGENT_OBSERVE_API_DURATION = Histogram(
    "codehub_agent_observe_api_duration_seconds",
    "Duration of individual observe sub-calls (containers, volumes, archives)",
    ["api"],  # containers, volumes, archives
    buckets=_BUCKETS_SLOW,
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

AGENT_PROXY_REQUESTS_TOTAL = Counter(
    "codehub_agent_proxy_requests_total",
    "Total proxied HTTP requests",
    ["method", "status_class"],
)

AGENT_PROXY_REQUEST_DURATION = Histogram(
    "codehub_agent_proxy_request_duration_seconds",
    "Proxied HTTP request duration (full roundtrip)",
    ["method"],
    buckets=_BUCKETS_FAST,
)

AGENT_PROXY_IN_FLIGHT = Gauge(
    "codehub_agent_proxy_in_flight",
    "Currently in-flight proxy requests",
)

AGENT_PROXY_UPSTREAM_ERRORS = Counter(
    "codehub_agent_proxy_upstream_errors_total",
    "Upstream connection errors",
    ["error_type"],
)

AGENT_PROXY_BYTES = Counter(
    "codehub_agent_proxy_bytes_total",
    "Total bytes proxied",
    ["direction"],
)

AGENT_PROXY_WS_ACTIVE = Gauge(
    "codehub_agent_proxy_ws_active",
    "Currently active WebSocket connections",
)

AGENT_PROXY_WS_CONNECT_TOTAL = Counter(
    "codehub_agent_proxy_ws_connect_total",
    "Total WebSocket connections established",
)

AGENT_PROXY_WS_CLOSE_TOTAL = Counter(
    "codehub_agent_proxy_ws_close_total",
    "Total WebSocket connections closed",
    ["close_code_class"],
)

AGENT_PROXY_WS_MESSAGES_TOTAL = Counter(
    "codehub_agent_proxy_ws_messages_total",
    "Total WebSocket messages relayed",
    ["direction"],
)

AGENT_PROXY_WS_ERRORS_TOTAL = Counter(
    "codehub_agent_proxy_ws_errors_total",
    "Total WebSocket proxy errors",
    ["error_type"],
)

AGENT_PROXY_UPSTREAM_CONNECT_DURATION = Histogram(
    "codehub_agent_proxy_upstream_connect_duration_seconds",
    "Time to establish connection to upstream workspace",
    buckets=_BUCKETS_FAST,
)

AGENT_PROXY_WS_SESSION_DURATION = Histogram(
    "codehub_agent_proxy_ws_session_duration_seconds",
    "WebSocket session duration",
    buckets=_BUCKETS_SLOW,
)


# =============================================================================
# Metric Initialization
# =============================================================================

def _init_metrics() -> None:
    """Initialize labeled metrics with zero values."""
    # Docker operations
    for op in ["list", "inspect", "create", "start", "stop", "remove", "wait", "logs", "volume_list", "volume_inspect", "volume_create", "volume_remove", "image_exists", "image_pull"]:
        _ = AGENT_DOCKER_DURATION.labels(operation=op)
        _ = AGENT_DOCKER_ERRORS.labels(operation=op, error_type="api_error")
        _ = AGENT_DOCKER_ERRORS.labels(operation=op, error_type="timeout")
        _ = AGENT_DOCKER_ERRORS.labels(operation=op, error_type="not_found")

    # S3 operations
    for op in ["list", "delete", "delete_batch", "exists", "get_object"]:
        _ = AGENT_S3_DURATION.labels(operation=op)
        _ = AGENT_S3_ERRORS.labels(operation=op, error_type="connection")
        _ = AGENT_S3_ERRORS.labels(operation=op, error_type="timeout")
        _ = AGENT_S3_ERRORS.labels(operation=op, error_type="not_found")

    # S3 transfer direction
    _ = AGENT_S3_BYTES.labels(direction="upload")
    _ = AGENT_S3_BYTES.labels(direction="download")

    for method in ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]:
        _ = AGENT_PROXY_REQUEST_DURATION.labels(method=method)
    for status in ["2xx", "3xx", "4xx", "5xx"]:
        for method in ["GET", "POST", "PUT", "DELETE", "PATCH"]:
            _ = AGENT_PROXY_REQUESTS_TOTAL.labels(method=method, status_class=status)
    for error_type in ["timeout", "refused", "reset", "other"]:
        _ = AGENT_PROXY_UPSTREAM_ERRORS.labels(error_type=error_type)
    _ = AGENT_PROXY_BYTES.labels(direction="in")
    _ = AGENT_PROXY_BYTES.labels(direction="out")

    for close_class in ["normal", "going_away", "error"]:
        _ = AGENT_PROXY_WS_CLOSE_TOTAL.labels(close_code_class=close_class)
    for direction in ["upstream", "downstream"]:
        _ = AGENT_PROXY_WS_MESSAGES_TOTAL.labels(direction=direction)
    for error_type in ["connect_failed", "relay_error", "handshake_failed"]:
        _ = AGENT_PROXY_WS_ERRORS_TOTAL.labels(error_type=error_type)

    # Observe API sub-calls
    for api in ["containers", "volumes", "archives"]:
        _ = AGENT_OBSERVE_API_DURATION.labels(api=api)


_init_metrics()
