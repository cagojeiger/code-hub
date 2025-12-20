"""Tests for SessionService."""

from datetime import datetime, timedelta

import pytest

from app.db import Session
from app.services.session_service import SessionService


@pytest.mark.asyncio
async def test_create_session(db_session, test_user):
    """Test session creation."""
    session = await SessionService.create(db_session, test_user.id)

    assert session.id is not None
    assert session.user_id == test_user.id
    assert session.created_at is not None
    # Compare as naive datetimes (SQLite stores without timezone)
    now = datetime.utcnow()
    expires_at = session.expires_at
    if expires_at.tzinfo is not None:
        expires_at = expires_at.replace(tzinfo=None)
    assert expires_at > now
    assert session.revoked_at is None


@pytest.mark.asyncio
async def test_get_valid_session(db_session, test_user):
    """Test getting a valid session."""
    # Create session
    created = await SessionService.create(db_session, test_user.id)

    # Get valid session
    session = await SessionService.get_valid(db_session, created.id)

    assert session is not None
    assert session.id == created.id


@pytest.mark.asyncio
async def test_get_valid_session_not_found(db_session):
    """Test getting a non-existent session."""
    session = await SessionService.get_valid(db_session, "nonexistent")

    assert session is None


@pytest.mark.asyncio
async def test_get_valid_with_user(db_session, test_user):
    """Test getting a valid session with user."""
    # Create session
    created = await SessionService.create(db_session, test_user.id)

    # Get valid session with user
    result = await SessionService.get_valid_with_user(db_session, created.id)

    assert result is not None
    session, user = result
    assert session.id == created.id
    assert user.id == test_user.id
    assert user.username == test_user.username


@pytest.mark.asyncio
async def test_revoke_session(db_session, test_user):
    """Test session revocation."""
    # Create session
    created = await SessionService.create(db_session, test_user.id)

    # Revoke session
    result = await SessionService.revoke(db_session, created.id)

    assert result is True

    # Session should no longer be valid
    await db_session.refresh(created)
    assert created.revoked_at is not None
    assert not SessionService.is_valid(created)


@pytest.mark.asyncio
async def test_revoke_nonexistent_session(db_session):
    """Test revoking a non-existent session."""
    result = await SessionService.revoke(db_session, "nonexistent")

    assert result is False


@pytest.mark.asyncio
async def test_is_valid_expired_session(db_session, test_user):
    """Test that expired sessions are not valid."""
    # Create a session with past expiry (use naive datetime for SQLite)
    session = Session(
        user_id=test_user.id,
        expires_at=datetime.utcnow() - timedelta(hours=1),
    )
    db_session.add(session)
    await db_session.commit()

    # Session should not be valid
    assert not SessionService.is_valid(session)


@pytest.mark.asyncio
async def test_is_valid_revoked_session(db_session, test_user):
    """Test that revoked sessions are not valid."""
    # Create a revoked session (use naive datetime for SQLite)
    session = Session(
        user_id=test_user.id,
        expires_at=datetime.utcnow() + timedelta(hours=24),
        revoked_at=datetime.utcnow(),
    )
    db_session.add(session)
    await db_session.commit()

    # Session should not be valid
    assert not SessionService.is_valid(session)


@pytest.mark.asyncio
async def test_get_valid_returns_none_for_expired(db_session, test_user):
    """Test that get_valid returns None for expired sessions."""
    # Create a session with past expiry (use naive datetime for SQLite)
    session = Session(
        user_id=test_user.id,
        expires_at=datetime.utcnow() - timedelta(hours=1),
    )
    db_session.add(session)
    await db_session.commit()

    # get_valid should return None
    result = await SessionService.get_valid(db_session, session.id)
    assert result is None


@pytest.mark.asyncio
async def test_get_valid_returns_none_for_revoked(db_session, test_user):
    """Test that get_valid returns None for revoked sessions."""
    # Create session
    created = await SessionService.create(db_session, test_user.id)

    # Revoke it
    await SessionService.revoke(db_session, created.id)

    # get_valid should return None
    result = await SessionService.get_valid(db_session, created.id)
    assert result is None
