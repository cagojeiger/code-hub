"""Prometheus metrics definitions for SQLAlchemy connection pool."""

from prometheus_client import Gauge

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
