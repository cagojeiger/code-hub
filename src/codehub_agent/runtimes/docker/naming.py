"""Resource naming utilities for Docker runtime."""

from codehub_agent.config import AgentConfig


class ResourceNaming:
    """Centralized naming conventions for Docker resources."""

    def __init__(self, config: AgentConfig) -> None:
        self._prefix = config.runtime.resource_prefix
        self._s3_bucket = config.s3.bucket
        self._archive_suffix = config.runtime.archive_suffix

    @property
    def prefix(self) -> str:
        return self._prefix

    @property
    def archive_suffix(self) -> str:
        return self._archive_suffix

    def container_name(self, workspace_id: str) -> str:
        return f"{self._prefix}{workspace_id}"

    def volume_name(self, workspace_id: str) -> str:
        return f"{self._prefix}{workspace_id}-home"

    def archive_s3_key(self, workspace_id: str, archive_op_id: str) -> str:
        return f"{self._prefix}{workspace_id}/{archive_op_id}/{self._archive_suffix}"

    def archive_s3_url(self, workspace_id: str, archive_op_id: str) -> str:
        key = self.archive_s3_key(workspace_id, archive_op_id)
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
