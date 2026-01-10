"""Prometheus metrics definitions.

Metrics are organized by the operational questions they answer:
- Q1: 지금 괜찮아? (Is it OK now?) - System health
- Q2: 뭐가 문제야? (What's broken?) - Failure identification
- Q3: 왜 문제야? (Why is it broken?) - Root cause analysis
- Q4: 성능은 괜찮아? (Is performance OK?) - SLO tracking
- Q5: 용량은 괜찮아? (Is capacity OK?) - Resource saturation

Multiprocess modes:
- liveall: Each worker reports independently (for worker-specific state)
- livesum: Sum across all workers (for per-worker resources like DB pools)
- livemax: Take max value (for shared data collected by leader only)
- Counter/Histogram: Automatically aggregated

See docs/LOGGING.md for alignment with log events.
"""

import os
from pathlib import Path

from prometheus_client import Counter, Gauge, Histogram

# Ensure multiprocess directory exists before creating gauges
# This is required because multiprocess_mode gauges need the directory at import time
_multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus_metrics")
Path(_multiproc_dir).mkdir(parents=True, exist_ok=True)
os.environ["PROMETHEUS_MULTIPROC_DIR"] = _multiproc_dir


# =============================================================================
# Q1: 지금 괜찮아? (Is it OK now?) - System Health
# =============================================================================

COORDINATOR_LEADER_INFO = Gauge(
    "codehub_coordinator_leader_info",
    "Whether this worker is the leader for the coordinator (1=leader, 0=follower)",
    ["coordinator", "worker_pid"],
    multiprocess_mode="liveall",
)

INFRA_UP = Gauge(
    "codehub_infra_up",
    "Infrastructure component connection status (1=connected, 0=disconnected)",
    ["component", "worker_pid"],
    multiprocess_mode="liveall",
)

WORKSPACES_IN_ERROR = Gauge(
    "codehub_workspaces_in_error",
    "Number of workspaces in ERROR phase",
    multiprocess_mode="livemax",
)


# =============================================================================
# Q2: 뭐가 문제야? (What's broken?) - Failure Identification
# =============================================================================

OPERATION_FAILURES_TOTAL = Counter(
    "codehub_operation_failures_total",
    "Total number of operation failures",
    ["operation", "error_class"],
)

OPERATION_IN_PROGRESS = Gauge(
    "codehub_operation_in_progress",
    "Number of operations currently in progress",
    ["operation"],
    multiprocess_mode="livemax",
)

WORKSPACES_STUCK = Gauge(
    "codehub_workspaces_stuck",
    "Number of workspaces stuck in an operation for too long",
    ["operation"],
    multiprocess_mode="livemax",
)


# =============================================================================
# Q3: 왜 문제야? (Why is it broken?) - Root Cause Analysis
# =============================================================================

RECONCILE_STAGE_DURATION = Histogram(
    "codehub_reconcile_stage_duration_seconds",
    "Duration of each reconcile stage",
    ["coordinator", "stage"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

INFRA_OPERATION_DURATION = Histogram(
    "codehub_infra_operation_duration_seconds",
    "Duration of infrastructure operations (docker, s3, db)",
    ["infra", "operation"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)


# =============================================================================
# Q4: 성능은 괜찮아? (Is performance OK?) - SLO Tracking
# =============================================================================

RECONCILE_DURATION = Histogram(
    "codehub_reconcile_duration_seconds",
    "Total duration of reconcile loop",
    ["coordinator"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

RECONCILE_TOTAL = Counter(
    "codehub_reconcile_total",
    "Total number of reconcile runs",
    ["coordinator", "status"],
)

OPERATION_TOTAL = Counter(
    "codehub_operation_total",
    "Total number of operations",
    ["operation", "status"],
)


# =============================================================================
# Q5: 용량은 괜찮아? (Is capacity OK?) - Resource Saturation
# =============================================================================

# DB Pool metrics use worker_pid label with liveall mode.
# Each worker has its own connection pool, so we report per-worker values.
# Dashboard queries should use sum() or avg() to aggregate across workers.
# Note: SQLAlchemy pool.overflow() returns negative values (-max_overflow) when
# no overflow connections exist, so collectors must use max(0, overflow).

DB_POOL_UTILIZATION = Gauge(
    "codehub_db_pool_utilization",
    "Database connection pool utilization ratio (0.0-1.0)",
    ["worker_pid"],
    multiprocess_mode="liveall",
)

DB_POOL_IDLE = Gauge(
    "codehub_db_pool_idle",
    "Number of idle database connections in pool",
    ["worker_pid"],
    multiprocess_mode="liveall",
)

DB_POOL_ACTIVE = Gauge(
    "codehub_db_pool_active",
    "Number of active database connections in use",
    ["worker_pid"],
    multiprocess_mode="liveall",
)

DB_POOL_OVERFLOW = Gauge(
    "codehub_db_pool_overflow",
    "Number of overflow database connections",
    ["worker_pid"],
    multiprocess_mode="liveall",
)

WORKSPACES_BY_PHASE = Gauge(
    "codehub_workspaces_by_phase",
    "Number of workspaces by phase",
    ["phase"],
    multiprocess_mode="livemax",
)

CONCURRENT_OPERATIONS = Gauge(
    "codehub_concurrent_operations",
    "Number of concurrent operations in progress",
    multiprocess_mode="livemax",
)


# =============================================================================
# Proxy WebSocket Metrics
# =============================================================================

PROXY_WS_ACTIVE_CONNECTIONS = Gauge(
    "codehub_proxy_ws_active_connections",
    "Currently active WebSocket proxy connections",
    multiprocess_mode="livesum",
)

PROXY_WS_MESSAGE_LATENCY = Histogram(
    "codehub_proxy_ws_message_latency_seconds",
    "WebSocket message relay latency",
    ["direction"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)

PROXY_WS_ERRORS = Counter(
    "codehub_proxy_ws_errors_total",
    "WebSocket proxy errors",
    ["error_type"],
)
