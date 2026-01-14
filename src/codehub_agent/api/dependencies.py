"""API dependencies for dependency injection."""

from codehub_agent.infra import get_s3_ops
from codehub_agent.runtimes import DockerRuntime

# Singleton runtime instance
_runtime: DockerRuntime | None = None


def init_runtime() -> None:
    """Initialize runtime singleton with dependencies.

    Must be called after init_s3() during app startup.
    """
    global _runtime
    _runtime = DockerRuntime(s3=get_s3_ops())


def get_runtime() -> DockerRuntime:
    """Get runtime singleton.

    Returns:
        DockerRuntime instance shared across all API endpoints.

    Raises:
        RuntimeError: If called before init_runtime().
    """
    if _runtime is None:
        raise RuntimeError("Runtime not initialized. Call init_runtime() first.")
    return _runtime


def reset_runtime() -> None:
    """Reset runtime singleton (for testing)."""
    global _runtime
    _runtime = None
