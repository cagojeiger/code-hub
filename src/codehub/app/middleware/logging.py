"""Request logging middleware.

Provides canonical log line per request with trace ID propagation.
"""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from codehub.app.config import get_settings
from codehub.app.logging import clear_trace_context, set_trace_id
from codehub.core.logging_schema import LogEvent

logger = logging.getLogger(__name__)
_settings = get_settings()
_logging_config = _settings.logging


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for request logging with trace ID propagation.

    Features:
    - Sets trace_id from X-Trace-ID header or generates new one
    - Logs canonical request log line (one per request)
    - Includes response status, duration, and path
    - Adds X-Trace-ID header to response

    Usage:
        app.add_middleware(LoggingMiddleware)
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Set trace ID from header or generate new one
        trace_id = request.headers.get("x-trace-id") or set_trace_id()
        if trace_id != request.headers.get("x-trace-id"):
            set_trace_id(trace_id)
        else:
            set_trace_id(trace_id)

        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            # Log failed requests
            duration_ms = (time.monotonic() - start) * 1000
            logger.error(
                "Request failed",
                extra={
                    "event": LogEvent.REQUEST_FAILED,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                    "trace_id": trace_id,
                },
            )
            raise
        finally:
            clear_trace_context()

        duration_ms = (time.monotonic() - start) * 1000

        # Skip logging for health checks, metrics, and static files to reduce noise
        skip_paths = ("/health", "/metrics", "/healthz", "/readyz")
        if not request.url.path.startswith("/static/") and request.url.path not in skip_paths:
            logger.info(
                "Request completed",
                extra={
                    "event": LogEvent.REQUEST_COMPLETE,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                    "trace_id": trace_id,
                },
            )

            # Slow request warning
            if duration_ms > _logging_config.slow_threshold_ms:
                logger.warning(
                    "Slow request detected",
                    extra={
                        "event": LogEvent.REQUEST_SLOW,
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "duration_ms": duration_ms,
                        "threshold_ms": _logging_config.slow_threshold_ms,
                        "trace_id": trace_id,
                    },
                )

        # Add trace ID to response header for debugging
        response.headers["X-Trace-ID"] = trace_id
        return response
