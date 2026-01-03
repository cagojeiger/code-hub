"""Database models for code-hub.

Models are defined using SQLModel (SQLAlchemy + Pydantic).
All models follow the schema defined in spec_v2/03-schema.md.

Note: Enum values are stored as strings to avoid circular imports.
      Use core.domain enums for type-safe operations in service layer.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Workspace(SQLModel, table=True):
    """Workspace model (M2 schema).

    Reference: docs/spec_v2/03-schema.md
    """

    __tablename__ = "workspaces"

    # Primary key
    id: str = Field(primary_key=True)  # UUID

    # Foreign keys
    owner_user_id: str = Field(foreign_key="users.id", index=True)

    # Basic fields
    name: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=500)
    memo: str | None = Field(default=None, sa_column=Column(Text))
    image_ref: str = Field(max_length=512)
    instance_backend: str  # 'local-docker' / 'k8s'
    storage_backend: str  # 'docker-volume' / 'minio'
    home_store_key: str = Field(max_length=512)  # ws-{id}-home
    home_ctx: dict | None = Field(default=None, sa_column=Column(JSONB))

    # M2 state fields (stored as str, convert to Enum in service layer)
    conditions: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="{}"),
    )
    phase: str = Field(default="PENDING")  # Phase enum value
    operation: str = Field(default="NONE")  # Operation enum value
    op_started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    op_id: str | None = None  # UUID for idempotency
    desired_state: str = Field(default="RUNNING")  # DesiredState enum value
    archive_key: str | None = Field(default=None, max_length=512)
    observed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    last_access_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    standby_ttl_seconds: int = Field(default=300)
    archive_ttl_seconds: int = Field(default=86400)
    error_reason: str | None = None  # ErrorReason enum value
    error_count: int = Field(default=0)

    # Timestamps
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    deleted_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), index=True)
    )

    __table_args__ = (
        # TTL Manager polling
        Index(
            "idx_workspaces_ttl_check",
            "phase",
            "operation",
            postgresql_where="deleted_at IS NULL AND phase IN ('RUNNING', 'STANDBY') AND operation = 'NONE'",
        ),
        # Reconciler target query
        Index(
            "idx_workspaces_reconcile",
            "phase",
            "desired_state",
            "operation",
            postgresql_where="deleted_at IS NULL",
        ),
        # In-progress operations
        Index(
            "idx_workspaces_operation",
            "operation",
            postgresql_where="deleted_at IS NULL AND operation != 'NONE'",
        ),
        # User running limit
        Index(
            "idx_workspaces_user_running",
            "owner_user_id",
            postgresql_where="deleted_at IS NULL AND phase = 'RUNNING'",
        ),
        # Global running count
        Index(
            "idx_workspaces_running",
            "phase",
            postgresql_where="deleted_at IS NULL AND phase = 'RUNNING'",
        ),
        # Error state query
        Index(
            "idx_workspaces_error",
            "phase",
            postgresql_where="deleted_at IS NULL AND phase = 'ERROR'",
        ),
    )
