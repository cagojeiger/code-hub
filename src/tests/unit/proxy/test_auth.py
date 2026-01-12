"""Tests for proxy authentication and caching.

Tests cache behavior for page load optimization:
- Session cache (3s TTL, cachetools.TTLCache)
- Workspace cache (3s TTL, cachetools.TTLCache)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codehub.app.proxy.auth import (
    clear_session_cache,
    clear_workspace_cache,
    get_user_id_from_session,
    get_workspace_for_user,
)
from codehub.core.errors import ForbiddenError, UnauthorizedError, WorkspaceNotFoundError
from codehub.core.models import Session, Workspace


@pytest.fixture(autouse=True)
def clear_caches():
    """Clear caches before and after each test."""
    clear_session_cache()
    clear_workspace_cache()
    yield
    clear_session_cache()
    clear_workspace_cache()


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return AsyncMock()


@pytest.fixture
def mock_session():
    """Create a mock Session object."""
    session = MagicMock(spec=Session)
    session.id = "session-123"
    session.user_id = "user-456"
    return session


@pytest.fixture
def mock_workspace():
    """Create a mock Workspace object."""
    workspace = MagicMock(spec=Workspace)
    workspace.id = "ws-789"
    workspace.owner_user_id = "user-456"
    workspace.deleted_at = None
    workspace.phase = "RUNNING"
    return workspace


class TestGetUserIdFromSession:
    """get_user_id_from_session() tests."""

    async def test_raises_unauthorized_when_cookie_is_none(self, mock_db):
        """Raises UnauthorizedError when session_cookie is None."""
        with pytest.raises(UnauthorizedError):
            await get_user_id_from_session(mock_db, None)

    async def test_raises_unauthorized_when_session_invalid(self, mock_db):
        """Raises UnauthorizedError when session is invalid."""
        with patch(
            "codehub.app.proxy.auth.SessionService.get_valid",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(UnauthorizedError):
                await get_user_id_from_session(mock_db, "invalid-session")

    async def test_returns_user_id_from_session(self, mock_db, mock_session):
        """Returns user_id from valid session."""
        with patch(
            "codehub.app.proxy.auth.SessionService.get_valid",
            new_callable=AsyncMock,
            return_value=mock_session,
        ):
            user_id = await get_user_id_from_session(mock_db, "session-123")
            assert user_id == "user-456"

    async def test_caches_session_result(self, mock_db, mock_session):
        """Caches session result for subsequent requests."""
        with patch(
            "codehub.app.proxy.auth.SessionService.get_valid",
            new_callable=AsyncMock,
            return_value=mock_session,
        ) as mock_get_valid:
            # First call - hits DB
            await get_user_id_from_session(mock_db, "session-123")
            assert mock_get_valid.call_count == 1

            # Second call - hits cache
            await get_user_id_from_session(mock_db, "session-123")
            assert mock_get_valid.call_count == 1  # Still 1

    async def test_cache_expires_after_ttl(self, mock_db, mock_session):
        """Cache expires after TTL."""
        with patch(
            "codehub.app.proxy.auth.SessionService.get_valid",
            new_callable=AsyncMock,
            return_value=mock_session,
        ) as mock_get_valid:
            # First call
            await get_user_id_from_session(mock_db, "session-123")
            assert mock_get_valid.call_count == 1

            # Clear cache to simulate TTL expiry (cachetools handles TTL internally)
            clear_session_cache("session-123")

            # Second call after "expiry"
            await get_user_id_from_session(mock_db, "session-123")
            assert mock_get_valid.call_count == 2

    async def test_removes_from_cache_when_session_revoked(self, mock_db, mock_session):
        """Removes session from cache when it becomes invalid."""
        call_count = 0

        async def get_valid_mock(_db, _session_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_session
            return None  # Session revoked

        with patch(
            "codehub.app.proxy.auth.SessionService.get_valid",
            side_effect=get_valid_mock,
        ):
            # First call - valid session
            user_id = await get_user_id_from_session(mock_db, "session-123")
            assert user_id == "user-456"

            # Clear cache to force DB check
            clear_session_cache("session-123")

            # Second call - session revoked
            with pytest.raises(UnauthorizedError):
                await get_user_id_from_session(mock_db, "session-123")


class TestGetWorkspaceForUser:
    """get_workspace_for_user() tests."""

    async def test_raises_not_found_when_workspace_missing(self, mock_db):
        """Raises WorkspaceNotFoundError when workspace doesn't exist."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(WorkspaceNotFoundError):
            await get_workspace_for_user(mock_db, "ws-missing", "user-456")

    async def test_raises_forbidden_when_not_owner(self, mock_db, mock_workspace):
        """Raises ForbiddenError when user doesn't own workspace."""
        mock_workspace.owner_user_id = "other-user"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_workspace
        mock_db.execute.return_value = mock_result

        with pytest.raises(ForbiddenError):
            await get_workspace_for_user(mock_db, "ws-789", "user-456")

    async def test_returns_workspace_when_owner(self, mock_db, mock_workspace):
        """Returns workspace when user is the owner."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_workspace
        mock_db.execute.return_value = mock_result

        workspace = await get_workspace_for_user(mock_db, "ws-789", "user-456")
        assert workspace.id == "ws-789"

    async def test_caches_workspace_result(self, mock_db, mock_workspace):
        """Caches workspace result for subsequent requests."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_workspace
        mock_db.execute.return_value = mock_result

        # First call - hits DB
        await get_workspace_for_user(mock_db, "ws-789", "user-456")
        assert mock_db.execute.call_count == 1

        # Second call - hits cache
        await get_workspace_for_user(mock_db, "ws-789", "user-456")
        assert mock_db.execute.call_count == 1  # Still 1

    async def test_cache_key_includes_user_id(self, mock_db, mock_workspace):
        """Cache key includes user_id (different users don't share cache)."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_workspace
        mock_db.execute.return_value = mock_result

        # Call with user-456
        await get_workspace_for_user(mock_db, "ws-789", "user-456")
        assert mock_db.execute.call_count == 1

        # Call with different user - cache miss
        mock_workspace.owner_user_id = "user-999"
        await get_workspace_for_user(mock_db, "ws-789", "user-999")
        assert mock_db.execute.call_count == 2

    async def test_cache_expires_after_ttl(self, mock_db, mock_workspace):
        """Cache expires after TTL."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_workspace
        mock_db.execute.return_value = mock_result

        # First call
        await get_workspace_for_user(mock_db, "ws-789", "user-456")
        assert mock_db.execute.call_count == 1

        # Clear cache to simulate TTL expiry
        clear_workspace_cache("ws-789", "user-456")

        # Second call after "expiry"
        await get_workspace_for_user(mock_db, "ws-789", "user-456")
        assert mock_db.execute.call_count == 2


class TestCacheClearFunctions:
    """Cache clear function tests."""

    async def test_clear_session_cache_clears_all(self, mock_db, mock_session):
        """clear_session_cache() clears all entries when called without args."""
        with patch(
            "codehub.app.proxy.auth.SessionService.get_valid",
            new_callable=AsyncMock,
            return_value=mock_session,
        ) as mock_get_valid:
            # Populate cache
            await get_user_id_from_session(mock_db, "session-1")
            mock_session.id = "session-2"
            await get_user_id_from_session(mock_db, "session-2")
            assert mock_get_valid.call_count == 2

            # Clear all
            clear_session_cache()

            # Both should miss cache
            mock_session.id = "session-1"
            await get_user_id_from_session(mock_db, "session-1")
            mock_session.id = "session-2"
            await get_user_id_from_session(mock_db, "session-2")
            assert mock_get_valid.call_count == 4

    async def test_clear_session_cache_clears_specific(self, mock_db, mock_session):
        """clear_session_cache() clears specific entry when session_id provided."""
        with patch(
            "codehub.app.proxy.auth.SessionService.get_valid",
            new_callable=AsyncMock,
            return_value=mock_session,
        ) as mock_get_valid:
            # Populate cache
            await get_user_id_from_session(mock_db, "session-1")
            mock_session.id = "session-2"
            await get_user_id_from_session(mock_db, "session-2")
            assert mock_get_valid.call_count == 2

            # Clear only session-1
            clear_session_cache("session-1")

            # session-1 should miss, session-2 should hit
            mock_session.id = "session-1"
            await get_user_id_from_session(mock_db, "session-1")
            assert mock_get_valid.call_count == 3

            await get_user_id_from_session(mock_db, "session-2")
            assert mock_get_valid.call_count == 3  # Still 3 (cache hit)

    async def test_clear_workspace_cache_clears_all(self, mock_db, mock_workspace):
        """clear_workspace_cache() clears all entries when called without args."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_workspace
        mock_db.execute.return_value = mock_result

        # Populate cache
        await get_workspace_for_user(mock_db, "ws-1", "user-456")
        mock_workspace.id = "ws-2"
        await get_workspace_for_user(mock_db, "ws-2", "user-456")
        assert mock_db.execute.call_count == 2

        # Clear all
        clear_workspace_cache()

        # Both should miss cache
        mock_workspace.id = "ws-1"
        await get_workspace_for_user(mock_db, "ws-1", "user-456")
        mock_workspace.id = "ws-2"
        await get_workspace_for_user(mock_db, "ws-2", "user-456")
        assert mock_db.execute.call_count == 4
