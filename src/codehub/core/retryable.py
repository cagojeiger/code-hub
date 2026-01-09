"""Retryable error classification with exponential backoff retry.

Classifies errors as retryable (transient) or non-retryable (permanent).
Used by coordinators to decide whether to retry operations.

Usage:
    from codehub.core.retryable import is_retryable, with_retry

    # Check if error is retryable
    if is_retryable(exc):
        # retry logic

    # Execute with automatic retry
    result = await with_retry(lambda: some_async_operation())
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
from botocore.exceptions import ClientError

from codehub.core.circuit_breaker import CircuitOpenError, get_circuit_breaker
from codehub.infra.docker import VolumeInUseError

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# httpx error classification
# =============================================================================

HTTPX_RETRYABLE = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
)

HTTPX_NON_RETRYABLE = (
    httpx.InvalidURL,
    httpx.TooManyRedirects,
)


def is_httpx_retryable(exc: Exception) -> bool:
    """Check if httpx exception is retryable."""
    if isinstance(exc, HTTPX_RETRYABLE):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        # 429 Rate limit - retryable
        if status == 429:
            return True
        # 4xx client errors - not retryable
        if 400 <= status < 500:
            return False
        # 5xx server errors - retryable
        if status >= 500:
            return True
    return False


# =============================================================================
# S3 (botocore) error classification
# =============================================================================

S3_RETRYABLE_CODES = frozenset({
    "RequestTimeout",
    "RequestTimeoutException",
    "ServiceUnavailable",
    "Throttling",
    "ThrottlingException",
    "TooManyRequestsException",
    "InternalError",
    "InternalServerError",
    "SlowDown",
})

S3_NON_RETRYABLE_CODES = frozenset({
    "AccessDenied",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
    "NoSuchBucket",
    "NoSuchKey",
    "InvalidBucketName",
})


def is_s3_retryable(exc: ClientError) -> bool:
    """Check if S3 ClientError is retryable."""
    error_code = exc.response.get("Error", {}).get("Code", "")
    return error_code in S3_RETRYABLE_CODES


# =============================================================================
# Unified classification
# =============================================================================


def is_retryable(exc: Exception) -> bool:
    """Check if error is retryable (transient).

    Args:
        exc: Exception to classify

    Returns:
        True if error is transient and operation can be retried
    """
    # asyncio timeout is retryable
    if isinstance(exc, asyncio.TimeoutError):
        return True

    # Docker volume in use - retryable (container deletion may free volume)
    if isinstance(exc, VolumeInUseError):
        return True

    # httpx errors
    if isinstance(exc, httpx.HTTPStatusError):
        return is_httpx_retryable(exc)
    if isinstance(exc, HTTPX_RETRYABLE):
        return True
    if isinstance(exc, HTTPX_NON_RETRYABLE):
        return False

    # S3 (botocore) errors
    if isinstance(exc, ClientError):
        return is_s3_retryable(exc)

    # Unknown errors - conservative: not retryable
    return False


def classify_error(exc: Exception) -> str:
    """Classify error as 'retryable', 'permanent', or 'unknown'.

    Args:
        exc: Exception to classify

    Returns:
        'retryable': Transient error, can retry
        'permanent': Permanent error, should not retry
        'unknown': Cannot classify
    """
    # asyncio timeout
    if isinstance(exc, asyncio.TimeoutError):
        return "retryable"

    # Docker volume in use - retryable (container deletion may free volume)
    if isinstance(exc, VolumeInUseError):
        return "retryable"

    # httpx errors
    if isinstance(exc, httpx.HTTPStatusError):
        return "retryable" if is_httpx_retryable(exc) else "permanent"
    if isinstance(exc, HTTPX_RETRYABLE):
        return "retryable"
    if isinstance(exc, HTTPX_NON_RETRYABLE):
        return "permanent"

    # S3 errors
    if isinstance(exc, ClientError):
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in S3_RETRYABLE_CODES:
            return "retryable"
        if error_code in S3_NON_RETRYABLE_CODES:
            return "permanent"
        return "unknown"

    return "unknown"


# =============================================================================
# Retry utility
# =============================================================================


async def with_retry(
    coro_factory: Callable[[], Awaitable[T]],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    circuit_breaker: str | None = None,
) -> T:
    """Execute async operation with exponential backoff retry.

    Only retries for retryable errors (transient failures).
    Non-retryable errors are raised immediately.

    Args:
        coro_factory: Factory function that creates new coroutine for each attempt
        max_retries: Maximum number of retry attempts (default: 3)
        base_delay: Initial delay in seconds (default: 1.0)
        max_delay: Maximum delay in seconds (default: 30.0)
        circuit_breaker: Circuit breaker name (None to disable)

    Returns:
        Result of successful operation

    Raises:
        CircuitOpenError: If circuit breaker is open
        Exception: The last exception if all retries fail, or immediately
                   for non-retryable errors

    Example:
        result = await with_retry(lambda: fetch_data())
        result = await with_retry(lambda: upload_file(), max_retries=5)
        result = await with_retry(lambda: call_api(), circuit_breaker="external")
    """
    cb = get_circuit_breaker(circuit_breaker) if circuit_breaker else None
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            if cb:
                return await cb.call(coro_factory)
            return await coro_factory()
        except CircuitOpenError:
            # Circuit is open - fail immediately without retry
            raise
        except Exception as exc:
            last_exc = exc
            error_class = classify_error(exc)

            # Non-retryable errors: fail immediately
            if error_class == "permanent":
                logger.warning(
                    "Permanent error (not retrying): %s",
                    exc,
                    extra={"error_class": error_class, "attempt": attempt + 1},
                )
                raise

            # Last attempt: raise the error
            if attempt == max_retries:
                logger.error(
                    "Max retries exceeded (%d attempts): %s",
                    max_retries + 1,
                    exc,
                    extra={"error_class": error_class, "attempt": attempt + 1},
                )
                raise

            # Calculate delay with exponential backoff + jitter
            delay = min(base_delay * (2**attempt), max_delay)
            # Jitter: 50% ~ 150% of delay (prevents thundering herd)
            jittered_delay = delay * (0.5 + random.random())
            logger.warning(
                "Retryable error (attempt %d/%d, retry in %.1fs): %s",
                attempt + 1,
                max_retries + 1,
                jittered_delay,
                exc,
                extra={
                    "error_class": error_class,
                    "attempt": attempt + 1,
                    "delay": jittered_delay,
                },
            )
            await asyncio.sleep(jittered_delay)

    # This should never be reached, but satisfy type checker
    if last_exc:
        raise last_exc
    raise RuntimeError("Unexpected state in with_retry")
