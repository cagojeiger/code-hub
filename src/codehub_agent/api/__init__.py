"""Agent API endpoints."""

from codehub_agent.api.health import router as health_router
from codehub_agent.api.instances import router as instances_router
from codehub_agent.api.jobs import router as jobs_router
from codehub_agent.api.storage import router as storage_router
from codehub_agent.api.volumes import router as volumes_router

__all__ = [
    "health_router",
    "instances_router",
    "jobs_router",
    "storage_router",
    "volumes_router",
]
