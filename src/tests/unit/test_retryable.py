"""Tests for retryable error classification and retry logic."""

import asyncio
from unittest.mock import MagicMock

import httpx
import pytest
from botocore.exceptions import ClientError

from codehub.core.retryable import (
    classify_error,
    is_httpx_retryable,
    is_retryable,
    is_s3_retryable,
    with_retry,
)


class TestHttpxRetryable:
    """Tests for httpx error classification."""

    def test_connect_error_is_retryable(self) -> None:
        """ConnectError should be retryable."""
        exc = httpx.ConnectError("connection failed")
        assert is_httpx_retryable(exc) is True
        assert is_retryable(exc) is True

    def test_connect_timeout_is_retryable(self) -> None:
        """ConnectTimeout should be retryable."""
        exc = httpx.ConnectTimeout("timeout")
        assert is_httpx_retryable(exc) is True
        assert is_retryable(exc) is True

    def test_read_timeout_is_retryable(self) -> None:
        """ReadTimeout should be retryable."""
        exc = httpx.ReadTimeout("read timeout")
        assert is_httpx_retryable(exc) is True

    def test_4xx_not_retryable(self) -> None:
        """4xx client errors should not be retryable."""
        request = httpx.Request("GET", "http://test.com")
        response = httpx.Response(400, request=request)
        exc = httpx.HTTPStatusError("bad request", request=request, response=response)
        assert is_httpx_retryable(exc) is False

    def test_429_is_retryable(self) -> None:
        """429 rate limit should be retryable."""
        request = httpx.Request("GET", "http://test.com")
        response = httpx.Response(429, request=request)
        exc = httpx.HTTPStatusError("rate limited", request=request, response=response)
        assert is_httpx_retryable(exc) is True

    def test_5xx_is_retryable(self) -> None:
        """5xx server errors should be retryable."""
        request = httpx.Request("GET", "http://test.com")
        response = httpx.Response(503, request=request)
        exc = httpx.HTTPStatusError(
            "service unavailable", request=request, response=response
        )
        assert is_httpx_retryable(exc) is True

    def test_invalid_url_not_retryable(self) -> None:
        """InvalidURL should not be retryable."""
        exc = httpx.InvalidURL("invalid url")
        assert is_retryable(exc) is False


class TestS3Retryable:
    """Tests for S3 (botocore) error classification."""

    def _make_client_error(self, code: str) -> ClientError:
        """Helper to create ClientError with specific code."""
        return ClientError(
            {"Error": {"Code": code, "Message": "test error"}},
            "TestOperation",
        )

    def test_throttling_is_retryable(self) -> None:
        """Throttling error should be retryable."""
        exc = self._make_client_error("Throttling")
        assert is_s3_retryable(exc) is True
        assert is_retryable(exc) is True

    def test_service_unavailable_is_retryable(self) -> None:
        """ServiceUnavailable should be retryable."""
        exc = self._make_client_error("ServiceUnavailable")
        assert is_s3_retryable(exc) is True

    def test_slow_down_is_retryable(self) -> None:
        """SlowDown should be retryable."""
        exc = self._make_client_error("SlowDown")
        assert is_s3_retryable(exc) is True

    def test_access_denied_not_retryable(self) -> None:
        """AccessDenied should not be retryable."""
        exc = self._make_client_error("AccessDenied")
        assert is_s3_retryable(exc) is False
        assert is_retryable(exc) is False

    def test_no_such_bucket_not_retryable(self) -> None:
        """NoSuchBucket should not be retryable."""
        exc = self._make_client_error("NoSuchBucket")
        assert is_s3_retryable(exc) is False

    def test_no_such_key_not_retryable(self) -> None:
        """NoSuchKey should not be retryable."""
        exc = self._make_client_error("NoSuchKey")
        assert is_s3_retryable(exc) is False


class TestVolumeInUseError:
    """Tests for Docker VolumeInUseError classification."""

    def test_volume_in_use_is_retryable(self) -> None:
        """VolumeInUseError should be retryable (container deletion may free volume)."""
        from codehub.infra.docker import VolumeInUseError

        exc = VolumeInUseError("Volume test-vol is in use by a container")
        assert is_retryable(exc) is True

    def test_volume_in_use_classified_as_retryable(self) -> None:
        """VolumeInUseError should be classified as retryable."""
        from codehub.infra.docker import VolumeInUseError

        exc = VolumeInUseError("Volume test-vol is in use")
        assert classify_error(exc) == "retryable"


class TestClassifyError:
    """Tests for classify_error function."""

    def test_asyncio_timeout_is_retryable(self) -> None:
        """asyncio.TimeoutError should be classified as retryable."""
        exc = asyncio.TimeoutError()
        assert classify_error(exc) == "retryable"
        assert is_retryable(exc) is True

    def test_connect_error_is_retryable(self) -> None:
        """httpx.ConnectError should be classified as retryable."""
        exc = httpx.ConnectError("connection failed")
        assert classify_error(exc) == "retryable"

    def test_4xx_is_permanent(self) -> None:
        """4xx errors should be classified as permanent."""
        request = httpx.Request("GET", "http://test.com")
        response = httpx.Response(404, request=request)
        exc = httpx.HTTPStatusError("not found", request=request, response=response)
        assert classify_error(exc) == "permanent"

    def test_s3_access_denied_is_permanent(self) -> None:
        """S3 AccessDenied should be classified as permanent."""
        exc = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}},
            "GetObject",
        )
        assert classify_error(exc) == "permanent"

    def test_s3_throttling_is_retryable(self) -> None:
        """S3 Throttling should be classified as retryable."""
        exc = ClientError(
            {"Error": {"Code": "Throttling", "Message": "Rate exceeded"}},
            "ListObjects",
        )
        assert classify_error(exc) == "retryable"

    def test_unknown_error_is_unknown(self) -> None:
        """Unknown errors should be classified as unknown."""
        exc = ValueError("some value error")
        assert classify_error(exc) == "unknown"

    def test_s3_unknown_code_is_unknown(self) -> None:
        """S3 error with unknown code should be classified as unknown."""
        exc = ClientError(
            {"Error": {"Code": "SomeUnknownCode", "Message": "Unknown"}},
            "SomeOperation",
        )
        assert classify_error(exc) == "unknown"


class TestWithRetry:
    """Tests for with_retry function."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self) -> None:
        """Should return result on first successful attempt."""
        call_count = 0

        async def success_func() -> str:
            nonlocal call_count
            call_count += 1
            return "success"

        result = await with_retry(success_func, max_retries=3)
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_retryable_error(self) -> None:
        """Should retry on retryable errors."""
        call_count = 0

        async def failing_then_success() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ConnectError("connection failed")
            return "success"

        result = await with_retry(
            failing_then_success,
            max_retries=3,
            base_delay=0.01,  # Fast for testing
        )
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_permanent_error(self) -> None:
        """Should not retry on permanent errors."""
        call_count = 0

        async def permanent_error() -> str:
            nonlocal call_count
            call_count += 1
            request = httpx.Request("GET", "http://test.com")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)

        with pytest.raises(httpx.HTTPStatusError):
            await with_retry(permanent_error, max_retries=3)

        assert call_count == 1  # No retry

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self) -> None:
        """Should raise after max retries exceeded."""
        call_count = 0

        async def always_fail() -> str:
            nonlocal call_count
            call_count += 1
            raise httpx.ConnectError("connection failed")

        with pytest.raises(httpx.ConnectError):
            await with_retry(
                always_fail,
                max_retries=2,
                base_delay=0.01,
            )

        assert call_count == 3  # Initial + 2 retries

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self) -> None:
        """Delay should increase exponentially."""
        import time

        call_times: list[float] = []

        async def record_time_and_fail() -> str:
            call_times.append(time.time())
            raise httpx.ConnectError("connection failed")

        with pytest.raises(httpx.ConnectError):
            await with_retry(
                record_time_and_fail,
                max_retries=2,
                base_delay=0.1,
                max_delay=1.0,
            )

        # Check delays are approximately exponential with jitter (50%~150%)
        # delay1: 0.1s * jitter = 0.05 ~ 0.15
        # delay2: 0.2s * jitter = 0.10 ~ 0.30
        assert len(call_times) == 3
        delay1 = call_times[1] - call_times[0]
        delay2 = call_times[2] - call_times[1]
        assert 0.04 < delay1 < 0.16  # 0.1s * (0.5~1.5) with margin
        assert 0.09 < delay2 < 0.31  # 0.2s * (0.5~1.5) with margin

    @pytest.mark.asyncio
    async def test_max_delay_cap(self) -> None:
        """Delay should be capped at max_delay."""
        import time

        call_times: list[float] = []

        async def record_time_and_fail() -> str:
            call_times.append(time.time())
            raise httpx.ConnectError("connection failed")

        with pytest.raises(httpx.ConnectError):
            await with_retry(
                record_time_and_fail,
                max_retries=3,
                base_delay=1.0,
                max_delay=0.1,  # Cap at 0.1s
            )

        # All delays should be capped at 0.1s
        for i in range(1, len(call_times)):
            delay = call_times[i] - call_times[i - 1]
            assert delay < 0.15  # Should be ~0.1s
