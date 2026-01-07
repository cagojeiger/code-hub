"""Prometheus metrics definitions for SQLAlchemy connection pool."""

import os
from pathlib import Path

from prometheus_client import Gauge

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
