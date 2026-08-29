"""Pull request schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class PullRequestResponse(ORMModel):
    id: str
    repository_id: str
    github_pr_number: int
    title: str
    author: str
    author_avatar_url: str | None = None
    base_branch: str | None = None
    head_branch: str | None = None
    base_sha: str
    head_sha: str
    state: str
    draft: bool
    html_url: str | None = None
    additions: int
    deletions: int
    changed_files: int
    opened_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PullRequestSummary(PullRequestResponse):
    """List view enriched with the latest review outcome."""

    repository_full_name: str | None = None
    body: str | None = None
    latest_review_id: str | None = None
    latest_review_status: str | None = None
    latest_review_risk_score: float | None = None
    latest_review_risk_band: str | None = None
    findings_count: int = 0
    review_count: int = 0


class PullRequestImportRequest(BaseModel):
    """Import (or refresh) a pull request from GitHub, then optionally review it."""

    number: int = Field(ge=1)
    review: bool = Field(default=True, description="Queue a review immediately after import")
