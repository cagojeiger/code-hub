"""Tests for Prometheus metrics definitions."""

import pytest
from prometheus_client import Counter, Gauge, Histogram


class TestBucketDefinitions:
    """Histogram bucket definition tests."""

    def test_buckets_fast_is_sorted(self):
        """FAST buckets are in ascending order."""
        from codehub.app.metrics.collector import _BUCKETS_FAST
        assert list(_BUCKETS_FAST) == sorted(_BUCKETS_FAST)

    def test_buckets_medium_is_sorted(self):
        """MEDIUM buckets are in ascending order."""
        from codehub.app.metrics.collector import _BUCKETS_MEDIUM
        assert list(_BUCKETS_MEDIUM) == sorted(_BUCKETS_MEDIUM)

    def test_buckets_slow_is_sorted(self):
        """SLOW buckets are in ascending order."""
        from codehub.app.metrics.collector import _BUCKETS_SLOW
        assert list(_BUCKETS_SLOW) == sorted(_BUCKETS_SLOW)

    def test_buckets_fast_range(self):
        """FAST buckets cover expected range (0.1ms to 5s)."""
        from codehub.app.metrics.collector import _BUCKETS_FAST
        assert _BUCKETS_FAST[0] == pytest.approx(0.0001, rel=0.1)  # 0.1ms
        assert _BUCKETS_FAST[-1] == pytest.approx(5, rel=0.1)  # 5s

    def test_buckets_medium_range(self):
        """MEDIUM buckets cover expected range (5ms to ~60s)."""
        from codehub.app.metrics.collector import _BUCKETS_MEDIUM
        assert _BUCKETS_MEDIUM[0] == pytest.approx(0.005, rel=0.1)  # 5ms
        assert _BUCKETS_MEDIUM[-1] > 50  # At least 50s

    def test_buckets_slow_range(self):
        """SLOW buckets cover expected range (100ms to 180s)."""
        from codehub.app.metrics.collector import _BUCKETS_SLOW
        assert _BUCKETS_SLOW[0] == pytest.approx(0.1, rel=0.1)  # 100ms
        assert _BUCKETS_SLOW[-1] == pytest.approx(180, rel=0.1)  # 180s

    def test_bucket_counts(self):
        """Bucket counts are within expected range (10-14)."""
        from codehub.app.metrics.collector import (
            _BUCKETS_FAST,
            _BUCKETS_MEDIUM,
            _BUCKETS_SLOW,
        )
        assert 10 <= len(_BUCKETS_FAST) <= 14
        assert 10 <= len(_BUCKETS_MEDIUM) <= 14
        assert 10 <= len(_BUCKETS_SLOW) <= 14


class TestMetricTypes:
    """Metric type verification tests."""

    def test_http_requests_total_is_counter(self):
        """HTTP_REQUESTS_TOTAL is a Counter."""
        from codehub.app.metrics.collector import HTTP_REQUESTS_TOTAL
        assert isinstance(HTTP_REQUESTS_TOTAL, Counter)

    def test_postgresql_pool_idle_is_gauge(self):
        """POSTGRESQL_POOL_IDLE is a Gauge."""
        from codehub.app.metrics.collector import POSTGRESQL_POOL_IDLE
        assert isinstance(POSTGRESQL_POOL_IDLE, Gauge)

    def test_http_request_duration_is_histogram(self):
        """HTTP_REQUEST_DURATION is a Histogram."""
        from codehub.app.metrics.collector import HTTP_REQUEST_DURATION
        assert isinstance(HTTP_REQUEST_DURATION, Histogram)

    def test_wc_cas_failures_total_is_counter(self):
        """WC_CAS_FAILURES_TOTAL is a Counter (no labels)."""
        from codehub.app.metrics.collector import WC_CAS_FAILURES_TOTAL
        assert isinstance(WC_CAS_FAILURES_TOTAL, Counter)


class TestMetricLabels:
    """Metric label verification tests."""

    def test_http_requests_total_has_labels(self):
        """HTTP_REQUESTS_TOTAL has method, endpoint, status labels."""
        from codehub.app.metrics.collector import HTTP_REQUESTS_TOTAL
        # Counter with labels returns a metric family with _labelnames
        assert "method" in HTTP_REQUESTS_TOTAL._labelnames
        assert "endpoint" in HTTP_REQUESTS_TOTAL._labelnames
        assert "status" in HTTP_REQUESTS_TOTAL._labelnames

    def test_workspaces_by_state_has_state_label(self):
        """WORKSPACES_BY_STATE has state label."""
        from codehub.app.metrics.collector import WORKSPACES_BY_STATE
        assert "state" in WORKSPACES_BY_STATE._labelnames

    def test_coordinator_reconcile_has_coordinator_label(self):
        """COORDINATOR_RECONCILE_TOTAL has coordinator label."""
        from codehub.app.metrics.collector import COORDINATOR_RECONCILE_TOTAL
        assert "coordinator" in COORDINATOR_RECONCILE_TOTAL._labelnames


class TestMetricInitialization:
    """_init_metrics() verification tests."""

    def test_init_metrics_callable(self):
        """_init_metrics function exists and is callable."""
        from codehub.app.metrics.collector import _init_metrics
        assert callable(_init_metrics)

    def test_workspace_states_initialized(self):
        """WORKSPACES_BY_STATE labels are initialized."""
        from codehub.app.metrics.collector import WORKSPACES_BY_STATE
        
        # Check that workspace state labels exist
        expected_states = ["running", "unhealthy", "stopped", "archived", "provisioning", "unknown"]
        for state in expected_states:
            # This should not raise - labels were initialized
            WORKSPACES_BY_STATE.labels(state=state)

    def test_circuit_breaker_initialized(self):
        """Circuit breaker metrics are initialized."""
        from codehub.app.metrics.collector import CIRCUIT_BREAKER_STATE
        
        # External circuit should be initialized
        CIRCUIT_BREAKER_STATE.labels(circuit="external")
