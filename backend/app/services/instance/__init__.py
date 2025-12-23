"""Instance Controller module for code-hub.

Provides container lifecycle management for workspaces.
MVP supports local-docker backend; k8s planned for cloud.
"""

from app.services.instance.interface import (
    InstanceController,
    InstanceStatus,
    UpstreamInfo,
)
from app.services.instance.local_docker import LocalDockerInstanceController

__all__ = [
    "InstanceController",
    "InstanceStatus",
    "LocalDockerInstanceController",
    "UpstreamInfo",
]
