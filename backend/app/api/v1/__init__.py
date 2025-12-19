"""API v1 module for code-hub."""

from fastapi import APIRouter

from app.api.v1.workspaces import router as workspaces_router

router = APIRouter(prefix="/api/v1")
router.include_router(workspaces_router)

__all__ = ["router"]
