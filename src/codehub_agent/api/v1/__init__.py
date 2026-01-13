"""API v1 module."""

from codehub_agent.api.v1.health import router as health_router
from codehub_agent.api.v1.instances import router as instances_router
from codehub_agent.api.v1.jobs import router as jobs_router
from codehub_agent.api.v1.storage import router as storage_router
from codehub_agent.api.v1.volumes import router as volumes_router

__all__ = [
    "health_router",
    "instances_router",
    "jobs_router",
    "storage_router",
    "volumes_router",
]
