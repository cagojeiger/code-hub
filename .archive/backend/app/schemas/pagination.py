"""Pagination schemas."""

from pydantic import BaseModel


class PaginationMeta(BaseModel):
    """Pagination metadata."""

    page: int
    per_page: int
    total: int
    total_pages: int
    has_next: bool
    has_prev: bool
