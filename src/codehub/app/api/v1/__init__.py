"""API v1 module."""

from codehub.app.api.v1.auth import router as auth_router
from codehub.app.api.v1.events import router as events_router
from codehub.app.api.v1.workspaces import router as workspaces_router

__all__ = ["auth_router", "events_router", "workspaces_router"]
