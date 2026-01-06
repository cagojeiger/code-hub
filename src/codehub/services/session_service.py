"""Session management service for code-hub.

Provides session lifecycle management:
- Create: Generate new session with TTL
- Get valid: Retrieve and validate session
- Revoke: Invalidate session
- Is valid: Check session validity

Configuration via SecurityConfig (SECURITY_ env prefix).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from codehub.app.config import get_settings
from codehub.core.models import Session, User

_security_config = get_settings().security


class SessionService:
    """Service for managing user sessions."""

    # Session TTL from config
    DEFAULT_SESSION_TTL_SECONDS = _security_config.session_ttl

    @staticmethod
    async def create(
        db: AsyncSession, user_id: str, ttl_seconds: int | None = None
    ) -> Session:
        """Create a new session for a user.

        Enforces single-session policy: deletes all existing sessions
        for the user before creating a new one.

        Args:
            db: Database session
            user_id: User ID to create session for
            ttl_seconds: Session TTL in seconds (defaults to 24 hours)

        Returns:
            Created session with expires_at set based on TTL
        """
        await db.execute(delete(Session).where(col(Session.user_id) == user_id))

        if ttl_seconds is None:
            ttl_seconds = SessionService.DEFAULT_SESSION_TTL_SECONDS

        session = Session(
            user_id=user_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    @staticmethod
    async def get_valid(db: AsyncSession, session_id: str) -> Session | None:
        """Get a valid session by ID.

        Returns None if session doesn't exist, is revoked, or is expired.

        Args:
            db: Database session
            session_id: Session ID to look up

        Returns:
            Valid session or None
        """
        result = await db.execute(
            select(Session).where(Session.id == session_id)  # type: ignore[arg-type]
        )
        session = result.scalar_one_or_none()

        if session is None:
            return None

        if not SessionService.is_valid(session):
            return None

        return session

    @staticmethod
    async def get_valid_with_user(
        db: AsyncSession, session_id: str
    ) -> tuple[Session, User] | None:
        """Get a valid session with its associated user.

        Returns None if session doesn't exist, is revoked, or is expired.

        Args:
            db: Database session
            session_id: Session ID to look up

        Returns:
            Tuple of (session, user) or None
        """
        result = await db.execute(
            select(Session, User)
            .join(User, Session.user_id == User.id)  # type: ignore[arg-type]
            .where(Session.id == session_id)  # type: ignore[arg-type]
        )
        row = result.one_or_none()

        if row is None:
            return None

        session, user = row
        if not SessionService.is_valid(session):
            return None

        return session, user

    @staticmethod
    async def revoke(db: AsyncSession, session_id: str) -> bool:
        """Revoke a session by setting revoked_at.

        Args:
            db: Database session
            session_id: Session ID to revoke

        Returns:
            True if session was revoked, False if not found
        """
        result = await db.execute(
            select(Session).where(Session.id == session_id)  # type: ignore[arg-type]
        )
        session = result.scalar_one_or_none()

        if session is None:
            return False

        session.revoked_at = datetime.now(UTC)
        await db.commit()
        return True

    @staticmethod
    def is_valid(session: Session) -> bool:
        """Check if a session is valid (not expired and not revoked).

        Args:
            session: Session to check

        Returns:
            True if session is valid
        """
        if session.revoked_at is not None:
            return False

        # Get current UTC time as naive datetime for consistent comparison
        # Database may store as naive or aware depending on driver
        now = datetime.now(UTC).replace(tzinfo=None)

        # Make expires_at naive if it's timezone-aware
        expires_at = session.expires_at
        if expires_at.tzinfo is not None:
            expires_at = expires_at.replace(tzinfo=None)

        return expires_at > now
