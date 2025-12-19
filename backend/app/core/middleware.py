"""Middleware for code-hub.

Provides cross-cutting concerns like request ID tracking.
"""

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import set_request_id

# Header name for request ID (standard convention)
REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Middleware that assigns a unique ID to each request.

    - Uses existing X-Request-ID header if present (for distributed tracing)
    - Generates new UUID if not present
    - Sets request_id in context for logging
    - Returns request_id in response header
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Process request and add request ID."""
        # Get existing request ID or generate new one
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())

        # Store in request state for access in route handlers
        request.state.request_id = request_id

        # Set in context for logging
        set_request_id(request_id)

        # Process request
        response = await call_next(request)

        # Add request ID to response header
        response.headers[REQUEST_ID_HEADER] = request_id

        return response
