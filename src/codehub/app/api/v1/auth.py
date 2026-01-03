"""Authentication API endpoints.

Endpoints:
- POST /api/v1/login - Login with username/password
- POST /api/v1/logout - Logout (revoke session)
- GET /api/v1/session - Get current session info
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codehub.core.errors import TooManyRequestsError, UnauthorizedError
from codehub.core.security import calculate_lockout_duration, verify_password
from codehub.infra import get_session
from codehub.infra.models import User
from codehub.services.session_service import SessionService

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    """Request schema for login."""

    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class SessionResponse(BaseModel):
    """Response schema for session info."""

    user_id: str
    username: str


DbSession = Annotated[AsyncSession, Depends(get_session)]


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    db: DbSession,
) -> SessionResponse:
    """Login with username and password.

    On success, sets a session cookie and returns user info.
    On failure, returns 401 Unauthorized.
    On too many failures, returns 429 Too Many Requests.
    """
    result = await db.execute(
        select(User).where(User.username == body.username)  # type: ignore[arg-type]
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise UnauthorizedError("Invalid username or password")

    # Check if account is locked
    now = datetime.now(UTC)
    if user.locked_until:
        # Ensure timezone-aware comparison
        locked_until = (
            user.locked_until.replace(tzinfo=UTC)
            if user.locked_until.tzinfo is None
            else user.locked_until
        )
        if locked_until > now:
            retry_after = int((locked_until - now).total_seconds())
            raise TooManyRequestsError(
                retry_after=retry_after,
                message=f"Too many failed attempts. Try again in {retry_after} seconds.",
            )

    # Verify password
    if not verify_password(body.password, user.password_hash):
        # Record failed attempt
        user.failed_login_attempts += 1
        user.last_failed_at = now

        # Calculate and set lockout if threshold exceeded
        lockout_seconds = calculate_lockout_duration(user.failed_login_attempts)
        if lockout_seconds > 0:
            user.locked_until = now + timedelta(seconds=lockout_seconds)

        await db.commit()
        raise UnauthorizedError("Invalid username or password")

    # Login successful - reset rate limiting fields
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_failed_at = None
    await db.commit()

    session = await SessionService.create(db, user.id)

    response.set_cookie(
        key="session",
        value=session.id,
        httponly=True,
        samesite="lax",
        secure=False,  # TODO: Set to True in production
        path="/",
        max_age=SessionService.DEFAULT_SESSION_TTL_SECONDS,
    )

    return SessionResponse(user_id=user.id, username=user.username)


@router.post("/logout")
async def logout(
    response: Response,
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> dict[str, str]:
    """Logout by revoking session and clearing cookie.

    Always succeeds (even if no session cookie present).
    """
    if session:
        await SessionService.revoke(db, session)

    response.delete_cookie(
        key="session",
        path="/",
    )

    return {"message": "Logged out"}


@router.get("/session")
async def get_session_info(
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> SessionResponse:
    """Get current session info.

    Returns 401 if not authenticated or session is invalid.
    """
    if session is None:
        raise UnauthorizedError()

    result = await SessionService.get_valid_with_user(db, session)

    if result is None:
        raise UnauthorizedError()

    _, user = result
    return SessionResponse(user_id=user.id, username=user.username)
