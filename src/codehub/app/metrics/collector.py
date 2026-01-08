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
