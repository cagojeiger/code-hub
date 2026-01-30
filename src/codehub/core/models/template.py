"""Template model for workspace templates.

Reference: docs/spec/03-schema.md
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text, BigInteger, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Template(SQLModel, table=True):
    """Workspace template model.
    
    Templates store archive metadata for reusing workspace initial states.
    Reuses archive/restore infrastructure (S3 storage, tar.zst format).
    """

    __tablename__ = "templates"

    id: str = Field(primary_key=True)
    owner_user_id: str = Field(foreign_key="users.id", index=True)

    name: str = Field(max_length=255)
    description: str | None = Field(default=None, sa_column=Column(Text))
    tags: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default="'[]'::jsonb"),
    )

    source_workspace_id: str = Field(max_length=26)
    archive_key: str = Field(max_length=512)
    image_ref: str = Field(max_length=512)
    storage_backend: str = Field(max_length=50)

    archive_size_bytes: int | None = Field(default=None, sa_column=Column(BigInteger))
    file_count: int | None = Field(default=None, sa_column=Column(Integer))

    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    deleted_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), index=True)
    )
