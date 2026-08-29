"""Review run and finding schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.enums import FindingStatus
from app.schemas.common import ORMModel


class FindingResponse(ORMModel):
    id: str
    review_run_id: str
    fingerprint: str
    file_path: str
    line_number: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    severity: str
    category: str
    title: str
    description: str
    evidence: str | None = None
    code_snippet: str | None = None
    suggested_fix: str | None = None
    confidence: float
    confidence_label: str
    source: str
    rule_id: str | None = None
    references: list[Any] | None = None
    related_symbols: list[Any] | None = None
    historical_context: dict[str, Any] | None = None
    status: str
    published: bool
    resolution_note: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime


class FindingWithContext(FindingResponse):
    repository_full_name: str | None = None
    pull_request_number: int | None = None
    commit_sha: str | None = None


class FindingStatusUpdate(BaseModel):
    status: str
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str) -> str:
        candidate = value.strip().lower()
        allowed = {member.value for member in FindingStatus}
        if candidate not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return candidate


class ReviewCommentResponse(ORMModel):
    id: str
    kind: str
    file_path: str | None = None
    line_number: int | None = None
    body: str
    github_comment_id: int | None = None
    github_url: str | None = None
    delivered: bool
    error_message: str | None = None
    created_at: datetime


class LLMUsageResponse(ORMModel):
    id: str
    provider: str
    model: str
    operation: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_cost_usd: float
    success: bool
    error_message: str | None = None
    created_at: datetime


class ReviewRunResponse(ORMModel):
    id: str
    pull_request_id: str
    commit_sha: str
    trigger: str
    status: str
    stage: str | None = None
    progress: int
    risk_score: float
    risk_band: str | None = None
    files_analyzed: int
    files_skipped: int
    findings_count: int
    suppressed_count: int
    summary: str | None = None
    published: bool
    github_review_url: str | None = None
    attempts: int
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    created_at: datetime


class ReviewRunSummary(ReviewRunResponse):
    repository_id: str | None = None
    repository_full_name: str | None = None
    pull_request_number: int | None = None
    pull_request_title: str | None = None
    pull_request_author: str | None = None


class ReviewRunDetail(ReviewRunSummary):
    risk_factors: list[Any] | None = None
    analysis: dict[str, Any] | None = None
    static_findings: list[Any] | None = None
    context_stats: dict[str, Any] | None = None
    findings: list[FindingResponse] = Field(default_factory=list)
    comments: list[ReviewCommentResponse] = Field(default_factory=list)
    llm_usage: list[LLMUsageResponse] = Field(default_factory=list)


class ReviewTriggerRequest(BaseModel):
    """Manually queue a review for a tracked pull request."""

    pull_request_id: str | None = None
    repository_id: str | None = None
    pr_number: int | None = Field(default=None, ge=1)
    force: bool = Field(
        default=False,
        description="Re-run even when a review already exists for the head commit",
    )


class ReviewEnqueuedResponse(BaseModel):
    review_run_id: str
    status: str
    created: bool
    task_id: str | None = None
    detail: str


class ReviewProgressResponse(BaseModel):
    review_run_id: str
    status: str
    stage: str | None = None
    progress: int
    findings_count: int
    error_message: str | None = None
    updated_at: datetime


class ReviewDiffFile(BaseModel):
    filename: str
    status: str
    additions: int
    deletions: int
    changes: int
    patch: str | None = None
    language: str | None = None
    findings: list[FindingResponse] = Field(default_factory=list)


class ReviewDiffResponse(BaseModel):
    review_run_id: str
    commit_sha: str
    files: list[ReviewDiffFile]
    truncated: bool = False
