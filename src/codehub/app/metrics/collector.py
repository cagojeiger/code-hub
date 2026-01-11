"""Prometheus metrics definitions for connection pools."""

import os
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram

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
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
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

COORDINATOR_TICK_TOTAL = Counter(
    "codehub_coordinator_tick_total",
    "Total number of coordinator ticks executed",
    ["coordinator"],
)

COORDINATOR_TICK_DURATION = Histogram(
    "codehub_coordinator_tick_duration_seconds",
    "Duration of coordinator tick execution",
    ["coordinator"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0),
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
    "codehub_observer_workspaces_total",
    "Number of workspaces observed",
    multiprocess_mode="livesum",
)

OBSERVER_CONTAINERS = Gauge(
    "codehub_observer_containers_total",
    "Number of containers observed",
    multiprocess_mode="livesum",
)

OBSERVER_VOLUMES = Gauge(
    "codehub_observer_volumes_total",
    "Number of volumes observed",
    multiprocess_mode="livesum",
)

OBSERVER_ARCHIVES = Gauge(
    "codehub_observer_archives_total",
    "Number of archives observed",
    multiprocess_mode="livesum",
)

# Observer operation durations
OBSERVER_LOAD_DURATION = Histogram(
    "codehub_observer_load_duration_seconds",
    "Duration to load workspace IDs from DB",
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0),
)

OBSERVER_OBSERVE_DURATION = Histogram(
    "codehub_observer_observe_duration_seconds",
    "Duration of parallel API observation (containers, volumes, archives)",
    buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0),
)

OBSERVER_UPDATE_DURATION = Histogram(
    "codehub_observer_update_duration_seconds",
    "Duration of bulk workspace conditions update",
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0),
)

OBSERVER_API_DURATION = Histogram(
    "codehub_observer_api_duration_seconds",
    "Duration of individual observation API calls",
    ["api"],  # containers, volumes, archives
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0),
)

# =============================================================================
# WorkspaceController Metrics
# =============================================================================
# Operation execution tracking

WC_OPERATIONS_TOTAL = Counter(
    "codehub_wc_operations_total",
    "Total workspace operations executed",
    ["operation"],
)

WC_OPERATION_DURATION = Histogram(
    "codehub_wc_operation_duration_seconds",
    "Duration of workspace operations",
    ["operation"],
    buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

WC_ERRORS_TOTAL = Counter(
    "codehub_wc_errors_total",
    "Total workspace operation errors",
    ["error_class"],
)

# WC stage durations (like Observer)
WC_LOAD_DURATION = Histogram(
    "codehub_wc_load_duration_seconds",
    "Duration to load workspaces from DB",
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0),
)

WC_PLAN_DURATION = Histogram(
    "codehub_wc_plan_duration_seconds",
    "Duration of judge + plan computation",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
)

WC_EXECUTE_DURATION = Histogram(
    "codehub_wc_execute_duration_seconds",
    "Duration of parallel execution (Docker/S3)",
    buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

WC_PERSIST_DURATION = Histogram(
    "codehub_wc_persist_duration_seconds",
    "Duration of CAS persist to DB",
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0),
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
    "Total TTL expirations (phase transitions)",
    ["transition"],
)

TTL_ACTIVITY_SYNCED_TOTAL = Counter(
    "codehub_ttl_activity_synced_total",
    "Total activities synced from Redis to DB",
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
