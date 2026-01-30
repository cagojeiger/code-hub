"""Template API endpoints."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from codehub.app.proxy.auth import get_user_id_from_session
from codehub.core.errors import ForbiddenError, WorkspaceNotFoundError
from codehub.infra import get_session
from codehub.services import template_service

router = APIRouter(prefix="/templates", tags=["templates"])

DbSession = Annotated[AsyncSession, Depends(get_session)]


class CreateTemplateRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None)
    tags: list[str] = Field(default_factory=list)


class UpdateTemplateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    tags: list[str] | None = None


class TemplateResponse(BaseModel):
    id: str
    owner_user_id: str
    name: str
    description: str | None
    tags: list[str]
    source_workspace_id: str
    archive_key: str
    image_ref: str
    storage_backend: str
    archive_size_bytes: int | None
    file_count: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TemplateListResponse(BaseModel):
    items: list[TemplateResponse]
    total: int


@router.post("", response_model=TemplateResponse, status_code=201)
async def create_template(
    request: CreateTemplateRequest,
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> TemplateResponse:
    user_id = await get_user_id_from_session(db, session)

    try:
        template = await template_service.create_template(
            db=db,
            user_id=user_id,
            workspace_id=request.workspace_id,
            name=request.name,
            description=request.description,
            tags=request.tags,
        )
    except WorkspaceNotFoundError:
        raise HTTPException(status_code=404, detail="Workspace not found")
    except ForbiddenError:
        raise HTTPException(status_code=403, detail="Not authorized")
    except template_service.WorkspaceNotStandbyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except template_service.WorkspaceNotArchivedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except template_service.TemplateNameConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return TemplateResponse.model_validate(template)


@router.get("", response_model=TemplateListResponse)
async def list_templates(
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> TemplateListResponse:
    user_id = await get_user_id_from_session(db, session)

    templates = await template_service.list_templates(
        db=db,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )

    return TemplateListResponse(
        items=[TemplateResponse.model_validate(t) for t in templates],
        total=len(templates),
    )


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: str,
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> TemplateResponse:
    user_id = await get_user_id_from_session(db, session)

    try:
        template = await template_service.get_template(
            db=db,
            template_id=template_id,
            user_id=user_id,
        )
    except template_service.TemplateNotFoundError:
        raise HTTPException(status_code=404, detail="Template not found")
    except ForbiddenError:
        raise HTTPException(status_code=403, detail="Not authorized")

    return TemplateResponse.model_validate(template)


@router.patch("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: str,
    request: UpdateTemplateRequest,
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> TemplateResponse:
    user_id = await get_user_id_from_session(db, session)

    try:
        template = await template_service.update_template(
            db=db,
            template_id=template_id,
            user_id=user_id,
            name=request.name,
            description=request.description,
            tags=request.tags,
        )
    except template_service.TemplateNotFoundError:
        raise HTTPException(status_code=404, detail="Template not found")
    except ForbiddenError:
        raise HTTPException(status_code=403, detail="Not authorized")
    except template_service.TemplateNameConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return TemplateResponse.model_validate(template)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    db: DbSession,
    session: Annotated[str | None, Cookie(alias="session")] = None,
) -> None:
    user_id = await get_user_id_from_session(db, session)

    try:
        await template_service.delete_template(
            db=db,
            template_id=template_id,
            user_id=user_id,
        )
    except template_service.TemplateNotFoundError:
        raise HTTPException(status_code=404, detail="Template not found")
    except ForbiddenError:
        raise HTTPException(status_code=403, detail="Not authorized")
