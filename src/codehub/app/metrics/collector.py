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
# PostgreSQL Pool Metrics
# =============================================================================
# These metrics are only measurable from the application
# (PostgreSQL doesn't know about SQLAlchemy pool)

DB_CONNECTED_WORKERS = Gauge(
    "codehub_db_connected_workers",
    "Number of workers connected to database",
    multiprocess_mode="livesum",
)

DB_POOL_IDLE = Gauge(
    "codehub_db_pool_idle",
    "Database connections idle in pool",
    multiprocess_mode="livesum",
)

DB_POOL_ACTIVE = Gauge(
    "codehub_db_pool_active",
    "Database connections in use",
    multiprocess_mode="livesum",
)

DB_POOL_TOTAL = Gauge(
    "codehub_db_pool_total",
    "Total database connections (idle + active)",
    multiprocess_mode="livesum",
)

DB_POOL_OVERFLOW = Gauge(
    "codehub_db_pool_overflow",
    "Database overflow connections (negative=headroom, positive=overflow)",
    multiprocess_mode="livesum",
)

# =============================================================================
# Redis Pool Metrics
# =============================================================================

REDIS_CONNECTED_WORKERS = Gauge(
    "codehub_redis_connected_workers",
    "Number of workers connected to Redis",
    multiprocess_mode="livesum",
)

REDIS_POOL_IDLE = Gauge(
    "codehub_redis_pool_idle",
    "Redis connections idle in pool",
    multiprocess_mode="livesum",
)

REDIS_POOL_ACTIVE = Gauge(
    "codehub_redis_pool_active",
    "Redis connections in use",
    multiprocess_mode="livesum",
)

REDIS_POOL_TOTAL = Gauge(
    "codehub_redis_pool_total",
    "Total Redis connections (idle + active)",
    multiprocess_mode="livesum",
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
