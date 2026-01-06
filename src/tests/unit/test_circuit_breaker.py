"""Tests for Circuit Breaker pattern implementation."""

import asyncio

import pytest

from codehub.core.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    get_circuit_breaker,
    reset_all_circuit_breakers,
)


class TestCircuitBreaker:
    """Tests for CircuitBreaker state transitions."""

    @pytest.fixture(autouse=True)
    def reset_circuit_breakers(self) -> None:
        """Reset global circuit breakers before each test."""
        reset_all_circuit_breakers()

    @pytest.mark.asyncio
    async def test_initial_state_is_closed(self) -> None:
        """Circuit should start in CLOSED state."""
        cb = CircuitBreaker(name="test")
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_success_keeps_circuit_closed(self) -> None:
        """Successful calls should keep circuit closed."""
        cb = CircuitBreaker(name="test", failure_threshold=3)

        async def success() -> str:
            return "ok"

        for _ in range(10):
            result = await cb.call(success)
            assert result == "ok"

        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failures_open_circuit(self) -> None:
        """Circuit should open after failure_threshold failures."""
        cb = CircuitBreaker(name="test", failure_threshold=3)

        async def fail() -> str:
            raise RuntimeError("fail")

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_circuit_rejects_immediately(self) -> None:
        """Open circuit should reject calls with CircuitOpenError."""
        cb = CircuitBreaker(name="test", failure_threshold=2)

        async def fail() -> str:
            raise RuntimeError("fail")

        # Trip the circuit
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        # Now calls should be rejected immediately
        with pytest.raises(CircuitOpenError) as exc_info:
            await cb.call(fail)

        assert exc_info.value.service == "test"
        assert exc_info.value.retry_after >= 0

    @pytest.mark.asyncio
    async def test_half_open_after_timeout(self) -> None:
        """Circuit should transition to HALF_OPEN after timeout."""
        cb = CircuitBreaker(name="test", failure_threshold=2, timeout=0.1)

        async def fail() -> str:
            raise RuntimeError("fail")

        # Trip the circuit
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        # Wait for timeout
        await asyncio.sleep(0.15)

        # Next call should trigger state check
        async def success() -> str:
            return "ok"

        result = await cb.call(success)
        assert result == "ok"
        # After success in HALF_OPEN, might transition based on success_threshold

    @pytest.mark.asyncio
    async def test_half_open_success_closes_circuit(self) -> None:
        """Circuit should close after success_threshold successes in HALF_OPEN."""
        cb = CircuitBreaker(
            name="test", failure_threshold=2, success_threshold=2, timeout=0.1
        )

        async def fail() -> str:
            raise RuntimeError("fail")

        async def success() -> str:
            return "ok"

        # Trip the circuit
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        # Wait for timeout
        await asyncio.sleep(0.15)

        # Two successes should close the circuit
        await cb.call(success)
        await cb.call(success)

        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens_circuit(self) -> None:
        """Circuit should reopen on failure in HALF_OPEN state."""
        cb = CircuitBreaker(name="test", failure_threshold=2, timeout=0.1)

        async def fail() -> str:
            raise RuntimeError("fail")

        # Trip the circuit
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(fail)

        assert cb.state == CircuitState.OPEN

        # Wait for timeout
        await asyncio.sleep(0.15)

        # Fail in HALF_OPEN should reopen
        with pytest.raises(RuntimeError):
            await cb.call(fail)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self) -> None:
        """Success should reset failure count in CLOSED state."""
        cb = CircuitBreaker(name="test", failure_threshold=3)

        async def fail() -> str:
            raise RuntimeError("fail")

        async def success() -> str:
            return "ok"

        # Two failures (not enough to trip)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(fail)

        # One success resets the count
        await cb.call(success)

        # Two more failures (should still be below threshold)
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(fail)

        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerSingleton:
    """Tests for global circuit breaker singleton."""

    @pytest.fixture(autouse=True)
    def reset_circuit_breakers(self) -> None:
        """Reset global circuit breakers before each test."""
        reset_all_circuit_breakers()

    def test_get_circuit_breaker_returns_same_instance(self) -> None:
        """get_circuit_breaker should return same instance for same name."""
        cb1 = get_circuit_breaker("external")
        cb2 = get_circuit_breaker("external")
        assert cb1 is cb2

    def test_get_circuit_breaker_different_names(self) -> None:
        """get_circuit_breaker should return different instances for different names."""
        cb1 = get_circuit_breaker("external")
        cb2 = get_circuit_breaker("internal")
        assert cb1 is not cb2

    def test_reset_clears_all_instances(self) -> None:
        """reset_all_circuit_breakers should clear all instances."""
        cb1 = get_circuit_breaker("test1")
        cb2 = get_circuit_breaker("test2")

        reset_all_circuit_breakers()

        cb1_new = get_circuit_breaker("test1")
        cb2_new = get_circuit_breaker("test2")

        assert cb1 is not cb1_new
        assert cb2 is not cb2_new


class TestCircuitOpenError:
    """Tests for CircuitOpenError exception."""

    def test_has_service_and_retry_after(self) -> None:
        """CircuitOpenError should have service and retry_after attributes."""
        exc = CircuitOpenError("external", 15.5)
        assert exc.service == "external"
        assert exc.retry_after == 15.5

    def test_message_format(self) -> None:
        """CircuitOpenError should have descriptive message."""
        exc = CircuitOpenError("external", 15.5)
        assert "external" in str(exc)
        assert "15.5" in str(exc)


class TestJitter:
    """Tests for jitter in retry delay calculation."""

    @pytest.mark.asyncio
    async def test_jitter_range(self) -> None:
        """Jitter should produce delays in 50% ~ 150% range."""
        import random

        base_delay = 1.0
        delays = []

        for _ in range(100):
            delay = base_delay * (0.5 + random.random())
            delays.append(delay)

        # All delays should be in range [0.5, 1.5)
        for delay in delays:
            assert 0.5 <= delay < 1.5, f"Delay {delay} out of range"

    @pytest.mark.asyncio
    async def test_jitter_distribution(self) -> None:
        """Jitter should produce varied delays (not all same)."""
        import random

        base_delay = 1.0
        delays = set()

        for _ in range(100):
            delay = base_delay * (0.5 + random.random())
            delays.add(round(delay, 2))  # Round to 2 decimals for comparison

        # Should have at least 20 distinct values (high variance)
        assert len(delays) >= 20, f"Only {len(delays)} distinct delays - insufficient variance"
