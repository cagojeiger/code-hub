"""Circuit Breaker pattern for external service calls.

States:
- CLOSED: Normal operation, requests pass through
- OPEN: Circuit tripped, requests fail immediately
- HALF_OPEN: Testing recovery, limited requests allowed

Global singleton instance for all external calls (Docker, S3).

Usage:
    from codehub.core.circuit_breaker import get_circuit_breaker

    cb = get_circuit_breaker("external")
    result = await cb.call(lambda: some_async_operation())
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TypeVar

from codehub.app.metrics.collector import (
    CIRCUIT_BREAKER_CALLS_TOTAL,
    CIRCUIT_BREAKER_REJECTIONS_TOTAL,
    CIRCUIT_BREAKER_STATE,
)
from codehub.core.logging_schema import LogEvent

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when circuit is open and request is rejected."""

    def __init__(self, service: str, retry_after: float) -> None:
        self.service = service
        self.retry_after = retry_after
        super().__init__(f"Circuit open for {service}, retry after {retry_after:.1f}s")


class CircuitBreaker:
    """Circuit Breaker with configurable thresholds.

    Args:
        name: Circuit breaker name for logging
        failure_threshold: Number of failures to open circuit (default: 5)
        success_threshold: Number of successes to close circuit (default: 2)
        timeout: Seconds to wait before half-open (default: 60.0)
        error_classifier: Function to classify errors. Returns 'permanent', 'retryable',
                         or 'unknown'. Permanent errors don't count toward failure threshold.
    """

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: float = 60.0,
        error_classifier: Callable[[Exception], str] | None = None,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout = timeout
        self._error_classifier = error_classifier

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        return self._state

    def _update_state_metric(self) -> None:
        """Update circuit breaker state metric."""
        state_value = {"closed": 0, "half_open": 1, "open": 2}[self._state.value]
        CIRCUIT_BREAKER_STATE.labels(circuit=self.name).set(state_value)

    async def call(
        self,
        coro_factory: Callable[[], Awaitable[T]],
    ) -> T:
        """Execute operation with circuit breaker protection.

        Args:
            coro_factory: Factory function that creates the coroutine

        Returns:
            Result of the operation

        Raises:
            CircuitOpenError: If circuit is open
            Exception: If operation fails
        """
        async with self._lock:
            self._check_state_transition()

            if self._state == CircuitState.OPEN:
                retry_after = self.timeout - (time.time() - (self._last_failure_time or 0))
                CIRCUIT_BREAKER_REJECTIONS_TOTAL.labels(circuit=self.name).inc()
                logger.warning(
                    "Circuit OPEN, rejecting request",
                    extra={
                        "event": LogEvent.OPERATION_FAILED,
                        "circuit": self.name,
                        "retry_after": max(0, retry_after),
                    },
                )
                raise CircuitOpenError(self.name, max(0, retry_after))

        try:
            result = await coro_factory()
            CIRCUIT_BREAKER_CALLS_TOTAL.labels(circuit=self.name, result="success").inc()
            await self._on_success()
            return result
        except Exception as exc:
            CIRCUIT_BREAKER_CALLS_TOTAL.labels(circuit=self.name, result="failure").inc()
            # Permanent errors (e.g., 404) should not count toward failure threshold
            if self._error_classifier:
                error_class = self._error_classifier(exc)
                if error_class == "permanent":
                    logger.debug(
                        "Permanent error, not counting as failure",
                        extra={
                            "circuit": self.name,
                            "error": str(exc),
                            "error_class": error_class,
                        },
                    )
                    raise
            await self._on_failure()
            raise

    def _check_state_transition(self) -> None:
        """Check if state should transition (OPEN -> HALF_OPEN)."""
        if self._state == CircuitState.OPEN:
            if (
                self._last_failure_time
                and time.time() - self._last_failure_time >= self.timeout
            ):
                logger.info(
                    "Transitioning OPEN -> HALF_OPEN",
                    extra={"event": LogEvent.STATE_CHANGED, "circuit": self.name},
                )
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                self._update_state_metric()

    async def _on_success(self) -> None:
        """Handle successful operation."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    logger.info(
                        "Transitioning HALF_OPEN -> CLOSED",
                        extra={
                            "event": LogEvent.STATE_CHANGED,
                            "circuit": self.name,
                            "success_count": self._success_count,
                        },
                    )
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._update_state_metric()
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0

    async def _on_failure(self) -> None:
        """Handle failed operation."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                logger.warning(
                    "Transitioning HALF_OPEN -> OPEN (failure)",
                    extra={"event": LogEvent.STATE_CHANGED, "circuit": self.name},
                )
                self._state = CircuitState.OPEN
                self._update_state_metric()
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    logger.warning(
                        "Transitioning CLOSED -> OPEN",
                        extra={
                            "event": LogEvent.STATE_CHANGED,
                            "circuit": self.name,
                            "failure_count": self._failure_count,
                        },
                    )
                    self._state = CircuitState.OPEN
                    self._update_state_metric()


_circuit_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str = "default",
    error_classifier: Callable[[Exception], str] | None = None,
) -> CircuitBreaker:
    """Get or create circuit breaker by name.

    Args:
        name: Circuit breaker name (default: "default")
        error_classifier: Function to classify errors. Only used when creating
                         a new circuit breaker. Ignored if circuit breaker already exists.

    Returns:
        CircuitBreaker instance
    """
    if name not in _circuit_breakers:
        _circuit_breakers[name] = CircuitBreaker(
            name=name,
            error_classifier=error_classifier,
        )
    return _circuit_breakers[name]


def reset_all_circuit_breakers() -> None:
    """Reset all circuit breakers (for testing)."""
    _circuit_breakers.clear()
