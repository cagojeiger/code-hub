"""API v1 module."""

from codehub_agent.api.v1.health import router as health_router
from codehub_agent.api.v1.proxy import router as proxy_router
from codehub_agent.api.v1.workspaces import router as workspaces_router

__all__ = [
    "health_router",
    "proxy_router",
    "workspaces_router",
]
