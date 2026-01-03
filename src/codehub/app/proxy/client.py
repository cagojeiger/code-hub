"""HTTP client management for workspace proxy.

Provides shared httpx AsyncClient for connection pooling and header filtering.
"""

import httpx

# =============================================================================
# Constants
# =============================================================================

# Proxy timeouts (seconds)
PROXY_TIMEOUT_TOTAL = 30.0  # Total request timeout
PROXY_TIMEOUT_CONNECT = 10.0  # Connection timeout

# HTTP hop-by-hop headers to remove before forwarding (RFC 7230)
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
    }
)

# WebSocket hop-by-hop headers (RFC 7230 + websockets library handles)
WS_HOP_BY_HOP_HEADERS = HOP_BY_HOP_HEADERS | frozenset(
    {
        "sec-websocket-key",  # websockets library generates
        "sec-websocket-version",  # websockets library sets
        "origin",  # Don't forward - causes 403 on code-server
    }
)

# =============================================================================
# HTTP Client Management
# =============================================================================

# Shared httpx client for connection pooling
_http_client: httpx.AsyncClient | None = None


async def get_http_client() -> httpx.AsyncClient:
    """Get or create shared httpx AsyncClient."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(PROXY_TIMEOUT_TOTAL, connect=PROXY_TIMEOUT_CONNECT)
        )
    return _http_client


async def close_http_client() -> None:
    """Close shared httpx client. Call on application shutdown."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


# =============================================================================
# Helper Functions
# =============================================================================


def filter_headers(headers: dict[str, str]) -> dict[str, str]:
    """Filter out hop-by-hop headers."""
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}
