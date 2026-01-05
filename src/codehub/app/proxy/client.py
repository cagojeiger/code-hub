"""HTTP client and header filtering for workspace proxy.

Configuration via ProxyConfig (PROXY_ env prefix).
"""

import asyncio

import httpx

from codehub.app.config import get_settings

_proxy_config = get_settings().proxy

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

WS_HOP_BY_HOP_HEADERS = HOP_BY_HOP_HEADERS | frozenset(
    {"sec-websocket-key", "sec-websocket-version", "origin"}
)

_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()


async def get_http_client() -> httpx.AsyncClient:
    """Get or create shared httpx AsyncClient."""
    global _http_client
    if _http_client is not None:
        return _http_client

    async with _http_client_lock:
        if _http_client is None:
            _http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    timeout=_proxy_config.timeout_total,
                    connect=_proxy_config.timeout_connect,
                    read=_proxy_config.timeout_total,
                    write=_proxy_config.timeout_total,
                    pool=_proxy_config.timeout_pool,
                ),
                limits=httpx.Limits(
                    max_connections=_proxy_config.max_connections,
                    max_keepalive_connections=_proxy_config.max_keepalive,
                    keepalive_expiry=_proxy_config.keepalive_expiry,
                ),
            )
    return _http_client


async def close_http_client() -> None:
    """Close shared httpx client."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def filter_headers(headers: dict[str, str]) -> dict[str, str]:
    """Filter out hop-by-hop headers."""
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}
