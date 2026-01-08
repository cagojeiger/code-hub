"""Prometheus metrics definitions for SQLAlchemy connection pool."""

import os
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram

# Ensure multiprocess directory exists before creating gauges
# This is required because multiprocess_mode gauges need the directory at import time
_multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus_metrics")
Path(_multiproc_dir).mkdir(parents=True, exist_ok=True)
os.environ["PROMETHEUS_MULTIPROC_DIR"] = _multiproc_dir

# PostgreSQL Pool metrics
# These metrics are only measurable from the application (PG doesn't know about SQLAlchemy pool)

DB_UP = Gauge(
    "codehub_db_up",
    "Database connection status (1=connected, 0=disconnected)",
    multiprocess_mode="livesum",
)

DB_POOL_CHECKEDIN = Gauge(
    "codehub_db_pool_checkedin",
    "Database connections idle in pool",
    multiprocess_mode="livesum",
)

DB_POOL_CHECKEDOUT = Gauge(
    "codehub_db_pool_checkedout",
    "Database connections in use",
    multiprocess_mode="livesum",
)

DB_POOL_OVERFLOW = Gauge(
    "codehub_db_pool_overflow",
    "Database overflow connections",
    multiprocess_mode="livesum",
)

# WebSocket metrics
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

# Workspace Lifecycle metrics
# These metrics track workspace state transitions and operation execution

WORKSPACE_STATE_TRANSITIONS = Counter(
    "codehub_workspace_state_transitions_total",
    "Total workspace state transitions",
    ["from_state", "to_state"],
)

WORKSPACE_OPERATIONS = Counter(
    "codehub_workspace_operations_total",
    "Total workspace operations initiated",
    ["operation", "status"],
)

WORKSPACE_OPERATION_DURATION = Histogram(
    "codehub_workspace_operation_duration_seconds",
    "Duration of workspace operations",
    ["operation"],
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

WORKSPACE_COUNT_BY_STATE = Gauge(
    "codehub_workspace_count_by_state",
    "Current number of workspaces by state",
    ["phase"],
    multiprocess_mode="livesum",
)

WORKSPACE_COUNT_BY_OPERATION = Gauge(
    "codehub_workspace_count_by_operation",
    "Current number of workspaces by active operation",
    ["operation"],
    multiprocess_mode="livesum",
)

WORKSPACE_TTL_EXPIRY = Counter(
    "codehub_workspace_ttl_expiry_total",
    "Total TTL expiry events",
    ["ttl_type"],
)

# Coordinator metrics
# These metrics track coordinator tick performance and health

COORDINATOR_TICK_DURATION = Histogram(
    "codehub_coordinator_tick_duration_seconds",
    "Duration of coordinator reconciliation tick",
    ["coordinator_type"],
    buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0),
)

COORDINATOR_TICK_TOTAL = Counter(
    "codehub_coordinator_tick_total",
    "Total coordinator ticks executed",
    ["coordinator_type", "status"],
)

COORDINATOR_LEADER_STATUS = Gauge(
    "codehub_coordinator_leader_status",
    "Leader election status (1=leader, 0=follower)",
    ["coordinator_type"],
    multiprocess_mode="max",
)

COORDINATOR_WC_RECONCILE_QUEUE = Gauge(
    "codehub_coordinator_wc_reconcile_queue",
    "Number of workspaces needing reconciliation",
    multiprocess_mode="livesum",
)

COORDINATOR_WC_CAS_FAILURES = Counter(
    "codehub_coordinator_wc_cas_failures_total",
    "Total CAS update failures in WC",
)

COORDINATOR_OBSERVER_API_DURATION = Histogram(
    "codehub_coordinator_observer_api_duration_seconds",
    "Duration of Observer API calls",
    ["resource_type"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0),
)

COORDINATOR_OBSERVER_API_ERRORS = Counter(
    "codehub_coordinator_observer_api_errors_total",
    "Total Observer API errors",
    ["resource_type", "error_type"],
)

COORDINATOR_GC_ORPHANS_DELETED = Counter(
    "codehub_coordinator_gc_orphans_deleted_total",
    "Total orphaned resources deleted",
    ["resource_type"],
)
