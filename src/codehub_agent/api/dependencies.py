"""API dependencies for dependency injection."""

from codehub_agent.runtimes import DockerRuntime

# Singleton runtime instance
_runtime: DockerRuntime | None = None


async def init_runtime() -> None:
    """Initialize runtime singleton.

    Creates DockerRuntime and initializes async resources (S3).
    Must be called during app startup.
    """
    global _runtime
    _runtime = DockerRuntime()
    await _runtime.init()


async def close_runtime() -> None:
    """Close runtime and release resources."""
    global _runtime
    if _runtime:
        await _runtime.close()
        _runtime = None


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
