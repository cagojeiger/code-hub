"""Workspace model (M2 schema).

Reference: docs/spec_v2/03-schema.md
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from codehub.core.domain.workspace import DesiredState, Operation, Phase


class Workspace(SQLModel, table=True):
    """Workspace model (M2 schema).

    Reference: docs/spec_v2/03-schema.md
    """

    __tablename__ = "workspaces"

    id: str = Field(primary_key=True)
    owner_user_id: str = Field(foreign_key="users.id", index=True)

    name: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=500)
    memo: str | None = Field(default=None, sa_column=Column(Text))
    image_ref: str = Field(max_length=512)
    instance_backend: str  # 'local-docker' / 'k8s'
    storage_backend: str  # 'docker-volume' / 'minio'
    home_store_key: str = Field(max_length=512)  # codehub-ws-{id}-home
    home_ctx: dict | None = Field(default=None, sa_column=Column(JSONB))

    conditions: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default="{}"),
    )
    phase: Phase = Field(default=Phase.PENDING, sa_type=String)
    operation: Operation = Field(default=Operation.NONE, sa_type=String)
    op_started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    op_id: str | None = None  # UUID for idempotency
    desired_state: DesiredState = Field(default=DesiredState.RUNNING, sa_type=String)
    archive_key: str | None = Field(default=None, max_length=512)
    observed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    last_access_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    phase_changed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    error_reason: str | None = None  # ErrorReason enum value
    error_count: int = Field(default=0)

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
