"""Authentication models (User, Session).

Models are defined using SQLModel (SQLAlchemy + Pydantic).
"""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel
from ulid import ULID


def generate_ulid() -> str:
    """Generate a new ULID string."""
    return str(ULID())


def utc_now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(UTC)


class User(SQLModel, table=True):
    """User account model."""

    __tablename__ = "users"

    id: str = Field(default_factory=generate_ulid, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(
        default_factory=utc_now, sa_column=Column(DateTime(timezone=True))
    )

    # Login rate limiting fields
    failed_login_attempts: int = Field(default=0)
    locked_until: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    last_failed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )


class Session(SQLModel, table=True):
    """Login session model."""

    __tablename__ = "sessions"

    id: str = Field(default_factory=generate_ulid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(
        default_factory=utc_now, sa_column=Column(DateTime(timezone=True))
    )
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    revoked_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
