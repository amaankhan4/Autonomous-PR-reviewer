"""Repository, settings and indexing schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.enums import Severity
from app.schemas.common import ORMModel


class RepositoryResponse(ORMModel):
    id: str
    github_repo_id: int
    installation_id: str
    owner: str
    name: str
    full_name: str
    description: str | None = None
    default_branch: str
    private: bool
    language: str | None = None
    html_url: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RepositoryDetail(RepositoryResponse):
    settings: RepositorySettingsResponse | None = None
    open_pull_requests: int = 0
    total_reviews: int = 0
    open_findings: int = 0
    last_indexed_at: datetime | None = None
    index_status: str | None = None
    indexed_files: int = 0


class RepositorySettingsResponse(ORMModel):
    id: str
    repository_id: str
    auto_review_enabled: bool
    review_on_open: bool
    review_on_synchronize: bool
    publish_to_github: bool
    min_severity: str
    max_files_per_review: int
    min_confidence: float
    excluded_paths: list[str] = Field(default_factory=list)
    enabled_analyzers: list[str] = Field(default_factory=list)
    llm_model: str | None = None
    review_event: str
    updated_at: datetime


class RepositorySettingsUpdate(BaseModel):
    """Partial update: only supplied fields are changed."""

    auto_review_enabled: bool | None = None
    review_on_open: bool | None = None
    review_on_synchronize: bool | None = None
    publish_to_github: bool | None = None
    min_severity: str | None = None
    max_files_per_review: int | None = Field(default=None, ge=1, le=300)
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    excluded_paths: list[str] | None = Field(default=None, max_length=200)
    enabled_analyzers: list[str] | None = None
    llm_model: str | None = Field(default=None, max_length=120)
    review_event: str | None = None

    @field_validator("min_severity")
    @classmethod
    def _valid_severity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip().upper()
        if candidate not in Severity.__members__:
            raise ValueError(f"min_severity must be one of {sorted(Severity.__members__)}")
        return candidate

    @field_validator("review_event")
    @classmethod
    def _valid_event(cls, value: str | None) -> str | None:
        if value is None:
            return None
        candidate = value.strip().upper()
        if candidate not in {"COMMENT", "REQUEST_CHANGES", "APPROVE"}:
            raise ValueError("review_event must be COMMENT, REQUEST_CHANGES or APPROVE")
        return candidate

    @field_validator("enabled_analyzers")
    @classmethod
    def _valid_analyzers(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        from app.analyzers.registry import ALL_ANALYZERS

        known = set(ALL_ANALYZERS)
        unknown = [item for item in value if item not in known]
        if unknown:
            raise ValueError(f"unknown analyzers: {', '.join(sorted(unknown))}")
        return value


class RepositoryIndexResponse(ORMModel):
    id: str
    repository_id: str
    commit_sha: str
    status: str
    files_indexed: int
    files_skipped: int
    chunks_indexed: int
    chunks_deleted: int
    incremental: bool
    duration_ms: int | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class ReindexRequest(BaseModel):
    full: bool = Field(default=False, description="Discard existing index state and rebuild")
    ref: str | None = Field(default=None, max_length=200)


class InstallationSyncResponse(BaseModel):
    installation_id: int
    account_login: str
    repositories_synced: int
    repositories_added: int
    demo_mode: bool


RepositoryDetail.model_rebuild()
