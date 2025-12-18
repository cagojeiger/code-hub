"""Database models for code-hub.

Models are defined using SQLModel (SQLAlchemy + Pydantic).
All models follow the schema defined in spec.md section 7.

Tables:
- users: User accounts
- sessions: Login sessions
- workspaces: Workspace metadata
"""

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Column, Text
from sqlmodel import Field, Relationship, SQLModel
from ulid import ULID

if TYPE_CHECKING:
    pass


def generate_ulid() -> str:
    """Generate a new ULID string."""
    return str(ULID())


def utc_now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


class WorkspaceStatus(str, Enum):
    """Workspace status as defined in spec.md.

    State transitions:
    - CREATED -> PROVISIONING (start)
    - PROVISIONING -> RUNNING (healthy) | ERROR (timeout/fail)
    - RUNNING -> STOPPING (stop) | ERROR (infra error)
    - STOPPING -> STOPPED (success) | ERROR (fail)
    - STOPPED -> PROVISIONING (start) | DELETING (delete)
    - ERROR -> PROVISIONING (start retry) | STOPPING (stop retry) | DELETING (delete)
    - DELETING -> DELETED (success) | ERROR (fail)
    - DELETED is terminal (soft delete)
    """

    CREATED = "CREATED"
    PROVISIONING = "PROVISIONING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    DELETING = "DELETING"
    ERROR = "ERROR"
    DELETED = "DELETED"


class User(SQLModel, table=True):
    """User account model.

    Attributes:
        id: Primary key (ULID)
        username: Login ID (unique)
        password_hash: Argon2id hashed password
        created_at: Account creation timestamp
    """

    __tablename__ = "users"

    id: str = Field(default_factory=generate_ulid, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(default_factory=utc_now)

    # Relationships
    sessions: list["Session"] = Relationship(back_populates="user")
    workspaces: list["Workspace"] = Relationship(back_populates="owner")


class Session(SQLModel, table=True):
    """Login session model.

    Sessions are used for authentication. The session ID is stored
    in a cookie and validated on each request.

    Attributes:
        id: Primary key (ULID, used as session cookie value)
        user_id: Foreign key to users table
        created_at: Session creation timestamp
        expires_at: Session expiration timestamp
        revoked_at: Logout/revocation timestamp (nullable)
    """

    __tablename__ = "sessions"

    id: str = Field(default_factory=generate_ulid, primary_key=True)
    user_id: str = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    revoked_at: datetime | None = Field(default=None)

    # Relationships
    user: User = Relationship(back_populates="sessions")


class Workspace(SQLModel, table=True):
    """Workspace metadata model.

    A workspace represents a cloud development environment instance.
    The actual container is managed by Instance Controller.

    Attributes:
        id: Primary key (ULID)
        owner_user_id: Foreign key to users table
        name: Workspace name
        description: Short description (optional)
        memo: Free-form memo (optional)
        status: Current workspace status
        image_ref: Container image reference
        instance_backend: Instance controller backend (local-docker)
        storage_backend: Storage provider backend (local-dir)
        home_store_key: Logical key for home storage
        home_ctx: Opaque context from storage provider (nullable)
        created_at: Creation timestamp
        updated_at: Last update timestamp
        deleted_at: Soft delete timestamp (nullable)
    """

    __tablename__ = "workspaces"

    id: str = Field(default_factory=generate_ulid, primary_key=True)
    owner_user_id: str = Field(foreign_key="users.id", index=True)
    name: str
    description: str | None = Field(default=None)
    memo: str | None = Field(default=None, sa_column=Column(Text))
    status: WorkspaceStatus = Field(default=WorkspaceStatus.CREATED)
    image_ref: str
    instance_backend: str = Field(default="local-docker")
    storage_backend: str = Field(default="local-dir")
    home_store_key: str
    home_ctx: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    deleted_at: datetime | None = Field(default=None, index=True)

    # Relationships
    owner: User = Relationship(back_populates="workspaces")
