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

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Circuit tripped, fail fast
    HALF_OPEN = "half_open"  # Testing recovery


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
        timeout: Seconds to wait before half-open (default: 30.0)
    """

    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout = timeout

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        return self._state

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
                logger.warning(
                    "[CircuitBreaker:%s] Circuit OPEN, rejecting request (retry_after=%.1fs)",
                    self.name,
                    max(0, retry_after),
                )
                raise CircuitOpenError(self.name, max(0, retry_after))

        try:
            result = await coro_factory()
            await self._on_success()
            return result
        except Exception:
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
                    "[CircuitBreaker:%s] Transitioning OPEN -> HALF_OPEN",
                    self.name,
                )
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0

    async def _on_success(self) -> None:
        """Handle successful operation."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    logger.info(
                        "[CircuitBreaker:%s] Transitioning HALF_OPEN -> CLOSED "
                        "(success_count=%d)",
                        self.name,
                        self._success_count,
                    )
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
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
                    "[CircuitBreaker:%s] Transitioning HALF_OPEN -> OPEN (failure)",
                    self.name,
                )
                self._state = CircuitState.OPEN
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    logger.warning(
                        "[CircuitBreaker:%s] Transitioning CLOSED -> OPEN "
                        "(failure_count=%d)",
                        self.name,
                        self._failure_count,
                    )
                    self._state = CircuitState.OPEN


# =============================================================================
# Global singleton instances
# =============================================================================

_circuit_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(name: str = "default") -> CircuitBreaker:
    """Get or create circuit breaker by name.

    Args:
        name: Circuit breaker name (default: "default")

    Returns:
        CircuitBreaker instance
    """
    if name not in _circuit_breakers:
        _circuit_breakers[name] = CircuitBreaker(name=name)
    return _circuit_breakers[name]


def reset_all_circuit_breakers() -> None:
    """Reset all circuit breakers (for testing)."""
    _circuit_breakers.clear()
