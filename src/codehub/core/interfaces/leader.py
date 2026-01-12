"""Leader election interface for PostgreSQL advisory lock."""

from abc import ABC, abstractmethod


class LeaderElection(ABC):
    """Abstract base class for leader election using PostgreSQL advisory lock.

    Implementations must handle:
    - Re-entrant check: Skip DB call if already leader
    - Parameter binding: Prevent SQL injection
    - Timeout: Prevent infinite wait on DB operations
    - Lock verification: Check pg_locks before critical operations
    """

    @property
    @abstractmethod
    def is_leader(self) -> bool:
        """Return True if this instance holds the leadership lock."""
        ...

    @property
    @abstractmethod
    def lock_id(self) -> int:
        """Return the computed lock ID for debugging/monitoring."""
        ...

    @abstractmethod
    async def try_acquire(self, timeout: float | None = None) -> bool:
        """Try to acquire leadership (non-blocking).

        Args:
            timeout: Query timeout in seconds.

        Returns:
            True if leadership acquired or already held.
        """
        ...

    @abstractmethod
    async def release(self, timeout: float | None = None) -> None:
        """Release leadership lock.

        Args:
            timeout: Query timeout in seconds.
        """
        ...

    @abstractmethod
    async def verify_holding(self, timeout: float | None = None) -> bool:
        """Verify that we still hold the advisory lock by querying pg_locks.

        Use this before critical operations to detect Split Brain scenarios
        where the connection may have been lost but is_leader is still True.

        Args:
            timeout: Query timeout in seconds.

        Returns:
            True if lock is still held, False otherwise.
        """
        ...
