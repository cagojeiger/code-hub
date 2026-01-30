"""Template service for workspace template management."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from codehub.core.domain import DesiredState, Phase
from codehub.core.errors import ForbiddenError, WorkspaceNotFoundError
from codehub.core.models import Template, Workspace, generate_ulid


class TemplateNotFoundError(Exception):
    """Raised when template is not found."""


class TemplateNameConflictError(Exception):
    """Raised when template name already exists for user."""


class WorkspaceNotStandbyError(Exception):
    """Raised when trying to create template from non-STANDBY workspace."""


class WorkspaceNotArchivedError(Exception):
    """Raised when workspace has no archive_key."""


async def create_template(
    db: AsyncSession,
    user_id: str,
    workspace_id: str,
    name: str,
    description: str | None = None,
    tags: list[str] | None = None,
) -> Template:
    """Create template from workspace archive.
    
    Args:
        db: Database session
        user_id: Template owner ID (must match workspace owner)
        workspace_id: Source workspace ID (must be STANDBY with archive)
        name: Template name
        description: Optional description
        tags: Optional tags for categorization
        
    Returns:
        Created template
        
    Raises:
        WorkspaceNotFoundError: Workspace not found or not owned by user
        WorkspaceNotStandbyError: Workspace not in STANDBY state
        WorkspaceNotArchivedError: Workspace has no archive_key
        TemplateNameConflictError: Template name already exists for user
    """
    stmt = select(Workspace).where(
        Workspace.id == workspace_id,
        Workspace.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    workspace = result.scalar_one_or_none()

    if workspace is None:
        raise WorkspaceNotFoundError()
    
    if workspace.owner_user_id != user_id:
        raise ForbiddenError()

    if workspace.phase != Phase.STANDBY.value:
        raise WorkspaceNotStandbyError(
            f"Workspace must be in STANDBY state (current: {workspace.phase})"
        )

    if not workspace.archive_key:
        raise WorkspaceNotArchivedError("Workspace has no archive_key")

    stmt = select(Template).where(
        Template.owner_user_id == user_id,
        Template.name == name,
        Template.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing is not None:
        raise TemplateNameConflictError(f"Template '{name}' already exists")

    now = datetime.now(UTC)
    template_id = generate_ulid()

    template = Template(
        id=template_id,
        owner_user_id=user_id,
        name=name,
        description=description,
        tags=tags or [],
        source_workspace_id=workspace_id,
        archive_key=workspace.archive_key,
        image_ref=workspace.image_ref,
        storage_backend=workspace.storage_backend,
        created_at=now,
        updated_at=now,
    )

    db.add(template)
    await db.commit()
    await db.refresh(template)

    return template


async def list_templates(
    db: AsyncSession,
    user_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list[Template]:
    """List templates owned by user.
    
    Args:
        db: Database session
        user_id: Template owner ID
        limit: Maximum number of templates to return
        offset: Number of templates to skip
        
    Returns:
        List of templates
    """
    stmt = (
        select(Template)
        .where(
            Template.owner_user_id == user_id,
            Template.deleted_at.is_(None),
        )
        .order_by(Template.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_template(
    db: AsyncSession,
    template_id: str,
    user_id: str | None = None,
) -> Template:
    """Get template by ID.
    
    Args:
        db: Database session
        template_id: Template ID
        user_id: If provided, verify ownership
        
    Returns:
        Template
        
    Raises:
        TemplateNotFoundError: Template not found
        ForbiddenError: User doesn't own template
    """
    stmt = select(Template).where(
        Template.id == template_id,
        Template.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    template = result.scalar_one_or_none()

    if template is None:
        raise TemplateNotFoundError()

    if user_id is not None and template.owner_user_id != user_id:
        raise ForbiddenError()

    return template


async def update_template(
    db: AsyncSession,
    template_id: str,
    user_id: str,
    name: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> Template:
    """Update template metadata.
    
    Args:
        db: Database session
        template_id: Template ID
        user_id: Template owner ID (for verification)
        name: New name (optional)
        description: New description (optional)
        tags: New tags (optional)
        
    Returns:
        Updated template
        
    Raises:
        TemplateNotFoundError: Template not found
        ForbiddenError: User doesn't own template
        TemplateNameConflictError: Name conflicts with existing template
    """
    template = await get_template(db, template_id, user_id)

    if name is not None and name != template.name:
        stmt = select(Template).where(
            Template.owner_user_id == user_id,
            Template.name == name,
            Template.id != template_id,
            Template.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is not None:
            raise TemplateNameConflictError(f"Template '{name}' already exists")

        template.name = name

    if description is not None:
        template.description = description

    if tags is not None:
        template.tags = tags

    template.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(template)

    return template


async def delete_template(
    db: AsyncSession,
    template_id: str,
    user_id: str,
) -> None:
    """Soft-delete template.
    
    Args:
        db: Database session
        template_id: Template ID
        user_id: Template owner ID (for verification)
        
    Raises:
        TemplateNotFoundError: Template not found
        ForbiddenError: User doesn't own template
    """
    template = await get_template(db, template_id, user_id)

    template.deleted_at = datetime.now(UTC)

    await db.commit()
