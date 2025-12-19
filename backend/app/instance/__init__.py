"""Instance Controller module for code-hub.

Provides container lifecycle management for workspaces.
MVP supports local-docker backend; k8s planned for cloud.
"""

from app.instance.interface import InstanceController, InstanceStatus, UpstreamInfo

__all__ = [
    "InstanceController",
    "InstanceStatus",
    "UpstreamInfo",
]
