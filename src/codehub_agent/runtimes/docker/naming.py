"""Resource naming utilities for Docker runtime."""

from codehub_agent.config import AgentConfig


class ResourceNaming:
    """Centralized naming conventions for Docker resources."""

    def __init__(self, config: AgentConfig) -> None:
        self._prefix = config.resource_prefix
        self._cluster_id = config.cluster_id
        self._s3_bucket = config.s3_bucket

    @property
    def prefix(self) -> str:
        return self._prefix

    def container_name(self, workspace_id: str) -> str:
        return f"{self._prefix}{workspace_id}"

    def volume_name(self, workspace_id: str) -> str:
        return f"{self._prefix}{workspace_id}-home"

    def archive_s3_key(self, workspace_id: str, op_id: str) -> str:
        return f"{self._cluster_id}/{workspace_id}/{op_id}/home.tar.zst"

    def archive_s3_url(self, workspace_id: str, op_id: str) -> str:
        key = self.archive_s3_key(workspace_id, op_id)
        return f"s3://{self._s3_bucket}/{key}"

    def workspace_id_from_container(self, container_name: str) -> str | None:
        if not container_name.startswith(self._prefix):
            return None
        return container_name[len(self._prefix) :]

    def workspace_id_from_volume(self, volume_name: str) -> str | None:
        suffix = "-home"
        if not volume_name.startswith(self._prefix) or not volume_name.endswith(suffix):
            return None
        return volume_name[len(self._prefix) : -len(suffix)]
