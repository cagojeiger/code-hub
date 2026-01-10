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
# PostgreSQL Configuration (Static - set once at startup)
# =============================================================================
# Use max mode so all workers report the same value and we get the config value
# Limit calculation: (pool_size + max_overflow) * count(connected_workers)

POSTGRESQL_POOL_SIZE = Gauge(
    "codehub_postgresql_pool_size",
    "PostgreSQL connection pool size setting (per worker)",
    multiprocess_mode="max",
)

POSTGRESQL_MAX_OVERFLOW = Gauge(
    "codehub_postgresql_max_overflow",
    "PostgreSQL max overflow setting (per worker)",
    multiprocess_mode="max",
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
# Redis Configuration (Static - set once at startup)
# =============================================================================

REDIS_MAX_CONNECTIONS = Gauge(
    "codehub_redis_max_connections",
    "Redis max connections setting (per worker)",
    multiprocess_mode="max",
)

# =============================================================================
# Worker Metrics
# =============================================================================

WORKERS_TOTAL = Gauge(
    "codehub_workers_total",
    "Total number of workers configured",
    multiprocess_mode="max",
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
