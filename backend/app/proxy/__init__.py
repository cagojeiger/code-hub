"""Workspace proxy module for code-hub.

Provides HTTP and WebSocket reverse proxy to workspace containers.
Routes: /w/{workspace_id}/* -> code-server container

Authentication and authorization is enforced via session cookies.
"""

from .client import close_http_client
from .router import router

__all__ = ["router", "close_http_client"]
