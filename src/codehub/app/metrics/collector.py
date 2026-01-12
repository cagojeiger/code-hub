"""Prometheus metrics definitions for connection pools."""

import os
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram

# =============================================================================
# Histogram Buckets (optimized by latency category)
# =============================================================================
# Bucket count: 10-14 (labeled histograms should be conservative)
# Scale: logarithmic with SLO boundaries (200ms, 1s, 5s)

# FAST: DB queries, CPU computation, Redis operations (0.5ms ~ 5s)
# Log scale: ratio ≈ 2.15
_BUCKETS_FAST = (
    0.0005, 0.001, 0.002, 0.005, 0.01,
    0.02, 0.05, 0.1, 0.2, 0.5,
    1, 2, 5,
)  # 13 buckets

# MEDIUM: API calls, external services, reconcile cycles (5ms ~ 60s)
# Log scale: ratio ≈ 2.04
_BUCKETS_MEDIUM = (
    0.005, 0.01, 0.02, 0.04, 0.09,
    0.18, 0.36, 0.73, 1.5, 3,
    6.2, 12.7, 26, 53,
)  # 14 buckets

# SLOW: Docker/S3 operations with 7 labels (100ms ~ 180s)
# Log scale: ratio ≈ 1.98
_BUCKETS_SLOW = (
    0.1, 0.2, 0.39, 0.77, 1.5,
    3, 6, 12, 23, 46,
    91, 180,
)  # 12 buckets

# Ensure multiprocess directory exists before creating gauges
# This is required because multiprocess_mode gauges need the directory at import time
_multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus_metrics")
Path(_multiproc_dir).mkdir(parents=True, exist_ok=True)
os.environ["PROMETHEUS_MULTIPROC_DIR"] = _multiproc_dir

# =============================================================================
# PostgreSQL Pool Metrics (Dynamic - per worker)
# =============================================================================
# These metrics are only measurable from the application
# (PostgreSQL doesn't know about SQLAlchemy pool)
#
# multiprocess_mode="all" automatically adds pid label for per-worker breakdown
# Prometheus adds instance label for per-pod identification
# Combined: instance + pid uniquely identifies each worker across all pods

POSTGRESQL_CONNECTED_WORKERS = Gauge(
    "codehub_postgresql_connected_workers",
    "Number of workers connected to PostgreSQL (1 if connected, 0 if not)",
    multiprocess_mode="all",
)

POSTGRESQL_POOL_IDLE = Gauge(
    "codehub_postgresql_pool_idle",
    "PostgreSQL connections idle in pool",
    multiprocess_mode="all",
)

POSTGRESQL_POOL_ACTIVE = Gauge(
    "codehub_postgresql_pool_active",
    "PostgreSQL connections in use",
    multiprocess_mode="all",
)

POSTGRESQL_POOL_TOTAL = Gauge(
    "codehub_postgresql_pool_total",
    "Total PostgreSQL connections (idle + active)",
    multiprocess_mode="all",
)

POSTGRESQL_POOL_OVERFLOW = Gauge(
    "codehub_postgresql_pool_overflow",
    "PostgreSQL overflow connections (negative=headroom, positive=overflow)",
    multiprocess_mode="all",
)

# =============================================================================
# Redis Pool Metrics (Dynamic - per worker)
# =============================================================================

REDIS_CONNECTED_WORKERS = Gauge(
    "codehub_redis_connected_workers",
    "Number of workers connected to Redis (1 if connected, 0 if not)",
    multiprocess_mode="all",
)

REDIS_POOL_IDLE = Gauge(
    "codehub_redis_pool_idle",
    "Redis connections idle in pool",
    multiprocess_mode="all",
)

REDIS_POOL_ACTIVE = Gauge(
    "codehub_redis_pool_active",
    "Redis connections in use",
    multiprocess_mode="all",
)

REDIS_POOL_TOTAL = Gauge(
    "codehub_redis_pool_total",
    "Total Redis connections (idle + active)",
    multiprocess_mode="all",
)

# =============================================================================
# WebSocket Metrics
# =============================================================================
# These metrics track WebSocket proxy performance and connection health

WS_ACTIVE_CONNECTIONS = Gauge(
    "codehub_ws_active_connections",
    "Currently active WebSocket connections",
    multiprocess_mode="livesum",
)

WS_MESSAGE_LATENCY = Histogram(
    "codehub_ws_message_latency_seconds",
    "WebSocket message relay latency",
    ["direction"],
    buckets=_BUCKETS_FAST,
)

WS_ERRORS = Counter(
    "codehub_ws_errors_total",
    "WebSocket connection errors",
    ["error_type"],
)

# =============================================================================
# Coordinator Metrics (Control Plane)
# =============================================================================
# These metrics track coordinator health and performance
# Only the leader coordinator updates these metrics

COORDINATOR_RECONCILE_TOTAL = Counter(
    "codehub_coordinator_reconcile_total",
    "Total number of coordinator reconcile cycles executed",
    ["coordinator"],
)

COORDINATOR_RECONCILE_DURATION = Histogram(
    "codehub_coordinator_reconcile_duration_seconds",
    "Duration of coordinator reconcile cycle execution",
    ["coordinator"],
    buckets=_BUCKETS_MEDIUM,
)

COORDINATOR_IS_LEADER = Gauge(
    "codehub_coordinator_is_leader",
    "Whether this instance is the leader (1) or not (0)",
    ["coordinator"],
    multiprocess_mode="livesum",
)

# =============================================================================
# Observer Metrics
# =============================================================================
# Resource counts from ObserverCoordinator observations

OBSERVER_WORKSPACES = Gauge(
    "codehub_observer_workspaces",
    "Number of workspaces observed",
    multiprocess_mode="livesum",
)

OBSERVER_CONTAINERS = Gauge(
    "codehub_observer_containers",
    "Number of containers observed",
    multiprocess_mode="livesum",
)

OBSERVER_VOLUMES = Gauge(
    "codehub_observer_volumes",
    "Number of volumes observed",
    multiprocess_mode="livesum",
)

OBSERVER_ARCHIVES = Gauge(
    "codehub_observer_archives",
    "Number of archives observed",
    multiprocess_mode="livesum",
)

# Observer stage durations (load, update) - fast DB operations
OBSERVER_STAGE_DURATION = Histogram(
    "codehub_observer_stage_duration_seconds",
    "Duration of observer stages",
    ["stage"],  # load, update
    buckets=_BUCKETS_FAST,
)

# Observer observe duration - slow external API calls
OBSERVER_OBSERVE_DURATION = Histogram(
    "codehub_observer_observe_duration_seconds",
    "Duration of observer observe stage",
    buckets=_BUCKETS_MEDIUM,
)

OBSERVER_API_DURATION = Histogram(
    "codehub_observer_api_duration_seconds",
    "Duration of individual observation API calls",
    ["api"],  # containers, volumes, archives
    buckets=_BUCKETS_MEDIUM,
)

# =============================================================================
# WorkspaceController Metrics
# =============================================================================
# WC stage durations (load, plan, persist) - fast DB/CPU operations
WC_STAGE_DURATION = Histogram(
    "codehub_wc_stage_duration_seconds",
    "Duration of WC stages",
    ["stage"],  # load, plan, persist
    buckets=_BUCKETS_FAST,
)

# WC execute duration - slow external operations (Docker/S3)
WC_EXECUTE_DURATION = Histogram(
    "codehub_wc_execute_duration_seconds",
    "Duration of WC execute stage",
    buckets=_BUCKETS_MEDIUM,
)

# Operation-level metrics for detailed latency analysis
WC_OPERATION_DURATION = Histogram(
    "codehub_wc_operation_duration_seconds",
    "Duration of WC operations",
    ["operation"],  # STARTING, STOPPING, PROVISIONING, DELETING, CREATE_EMPTY_ARCHIVE, ARCHIVING, RESTORING
    buckets=_BUCKETS_SLOW,
)

WC_CAS_FAILURES_TOTAL = Counter(
    "codehub_wc_cas_failures_total",
    "Total CAS update failures",
)


# =============================================================================
# TTL Manager Metrics
# =============================================================================
# TTL expiration and activity sync tracking

TTL_EXPIRATIONS_TOTAL = Counter(
    "codehub_ttl_expirations_total",
    "Idle workspace auto-transitions (power saving)",
    ["transition"],  # running_to_standby, standby_to_archived
)

TTL_SYNC_DURATION = Histogram(
    "codehub_ttl_sync_duration_seconds",
    "Duration of TTL sync operations",
    ["target"],  # redis, db
    buckets=_BUCKETS_FAST,
)

# =============================================================================
# EventListener Metrics
# =============================================================================
# CDC (Change Data Capture) event pipeline tracking

EVENT_NOTIFY_RECEIVED_TOTAL = Counter(
    "codehub_event_notify_received_total",
    "Total PostgreSQL NOTIFY events received",
    ["channel"],  # ws_sse, ws_wake
)

EVENT_SSE_PUBLISHED_TOTAL = Counter(
    "codehub_event_sse_published_total",
    "Total SSE events published to Redis",
)

EVENT_WAKE_PUBLISHED_TOTAL = Counter(
    "codehub_event_wake_published_total",
    "Total wake events published to Redis",
    ["target"],  # ob, wc
)

EVENT_QUEUE_SIZE = Gauge(
    "codehub_event_queue_size",
    "Current event queue size (backlog)",
    multiprocess_mode="livesum",
)

EVENT_ERRORS_TOTAL = Counter(
    "codehub_event_errors_total",
    "Total EventListener errors",
    ["operation"],  # sse, wake
)

# Coordinator wake reception tracking
COORDINATOR_WAKE_RECEIVED_TOTAL = Counter(
    "codehub_coordinator_wake_received_total",
    "Total wake events received by coordinator",
    ["coordinator"],
)

# EventListener leadership (separate from CoordinatorBase)
EVENT_LISTENER_IS_LEADER = Gauge(
    "codehub_event_listener_is_leader",
    "Whether EventListener is the leader (1) or not (0)",
    multiprocess_mode="livesum",
)

# =============================================================================
# Circuit Breaker Metrics
# =============================================================================
# Track circuit breaker state and external service health

CIRCUIT_BREAKER_STATE = Gauge(
    "codehub_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=half_open, 2=open)",
    ["circuit"],
    multiprocess_mode="livesum",
)

CIRCUIT_BREAKER_CALLS_TOTAL = Counter(
    "codehub_circuit_breaker_calls_total",
    "Total circuit breaker calls",
    ["circuit", "result"],  # result: success, failure
)

CIRCUIT_BREAKER_REJECTIONS_TOTAL = Counter(
    "codehub_circuit_breaker_rejections_total",
    "Total requests rejected due to open circuit",
    ["circuit"],
)

# =============================================================================
# External Call Metrics
# =============================================================================
# Error classification for retry logic (independent of circuit breaker)

EXTERNAL_CALL_ERRORS_TOTAL = Counter(
    "codehub_external_call_errors_total",
    "Total external call errors by type",
    ["error_type"],  # retryable, permanent, unknown, circuit_open
)


# =============================================================================
# Metric Initialization (ensure labels appear before first use)
# =============================================================================


def _init_metrics() -> None:
    """Initialize labeled metrics with zero values.

    Prometheus metrics with labels don't appear in output until first use.
    This causes "nodata" in Grafana. Initialize all labeled metrics here
    so they show 0 instead of nodata.
    """
    # Circuit Breaker (critical - may never be called if no external ops)
    CIRCUIT_BREAKER_STATE.labels(circuit="external").set(0)  # 0 = closed
    CIRCUIT_BREAKER_CALLS_TOTAL.labels(circuit="external", result="success")
    CIRCUIT_BREAKER_CALLS_TOTAL.labels(circuit="external", result="failure")
    CIRCUIT_BREAKER_REJECTIONS_TOTAL.labels(circuit="external")

    # External Call Errors
    EXTERNAL_CALL_ERRORS_TOTAL.labels(error_type="retryable")
    EXTERNAL_CALL_ERRORS_TOTAL.labels(error_type="permanent")
    EXTERNAL_CALL_ERRORS_TOTAL.labels(error_type="unknown")
    EXTERNAL_CALL_ERRORS_TOTAL.labels(error_type="circuit_open")

    # TTL Expirations (may never happen if workspaces are active)
    TTL_EXPIRATIONS_TOTAL.labels(transition="running_to_standby")
    TTL_EXPIRATIONS_TOTAL.labels(transition="standby_to_archived")

    # Event Errors (hopefully never called, but show 0 not nodata)
    EVENT_ERRORS_TOTAL.labels(operation="sse")
    EVENT_ERRORS_TOTAL.labels(operation="wake")

    # WebSocket Errors
    WS_ERRORS.labels(error_type="invalid_uri")
    WS_ERRORS.labels(error_type="handshake_failed")
    WS_ERRORS.labels(error_type="connection_failed")
    WS_ERRORS.labels(error_type="connection_closed")
    WS_ERRORS.labels(error_type="relay_error")

    # WC Operations (may never happen if no state changes)
    for op in ["STARTING", "STOPPING", "PROVISIONING", "DELETING", "CREATE_EMPTY_ARCHIVE", "ARCHIVING", "RESTORING"]:
        WC_OPERATION_DURATION.labels(operation=op)

    # Stage durations (labeled histograms)
    for stage in ["load", "update"]:
        OBSERVER_STAGE_DURATION.labels(stage=stage)
    for stage in ["load", "plan", "persist"]:
        WC_STAGE_DURATION.labels(stage=stage)
    for target in ["redis", "db"]:
        TTL_SYNC_DURATION.labels(target=target)


_init_metrics()
