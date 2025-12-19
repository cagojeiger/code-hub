"""Instance Controller module for code-hub.

Provides container lifecycle management for workspaces.
MVP supports local-docker backend; k8s planned for cloud.
"""

from app.instance.interface import InstanceController, InstanceStatus, UpstreamInfo
from app.instance.local_docker import LocalDockerInstanceController

__all__ = [
    "InstanceController",
    "InstanceStatus",
    "LocalDockerInstanceController",
    "UpstreamInfo",
]
