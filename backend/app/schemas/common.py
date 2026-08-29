"""Shared response envelopes and pagination."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    """Offset pagination envelope used by every list endpoint."""

    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    pages: int = Field(ge=0)

    @classmethod
    def build(cls, items: list[T], total: int, page: int, page_size: int) -> Page[T]:
        pages = (total + page_size - 1) // page_size if page_size else 0
        return cls(items=items, total=total, page=page, page_size=page_size, pages=pages)


class Message(BaseModel):
    detail: str


class ErrorResponse(BaseModel):
    error: str
    detail: str
    code: str | None = None
    request_id: str | None = None
    fields: dict[str, str] | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    demo_mode: bool
    checks: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime
