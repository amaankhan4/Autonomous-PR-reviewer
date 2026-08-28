"""Review runs, findings, published GitHub comments and LLM usage records."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import FindingStatus, ReviewStatus
from app.db.types import GUID, JSONType
from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.pull_request import PullRequest


class ReviewRun(UUIDMixin, TimestampMixin, Base):
    """A single review of a pull request at a specific commit.

    ``(pull_request_id, commit_sha)`` is unique, which is what makes webhook
    delivery idempotent: repeated ``synchronize`` events for the same head SHA
    resolve to the same review run instead of creating duplicate work.
    """

    __tablename__ = "review_runs"
    __table_args__ = (
        UniqueConstraint("pull_request_id", "commit_sha", name="uq_review_run_pr_commit"),
        Index("ix_review_runs_pr", "pull_request_id"),
        Index("ix_review_runs_commit_sha", "commit_sha"),
        Index("ix_review_runs_status", "status"),
        Index("ix_review_runs_created_at", "created_at"),
    )

    pull_request_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger: Mapped[str] = mapped_column(String(40), default="webhook", nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=ReviewStatus.QUEUED.value, nullable=False
    )
    stage: Mapped[str | None] = mapped_column(String(60))
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    risk_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    risk_band: Mapped[str | None] = mapped_column(String(20))
    risk_factors: Mapped[list | None] = mapped_column(JSONType())

    files_analyzed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    files_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    findings_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    suppressed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    summary: Mapped[str | None] = mapped_column(Text)
    analysis: Mapped[dict | None] = mapped_column(JSONType())
    static_findings: Mapped[list | None] = mapped_column(JSONType())
    context_stats: Mapped[dict | None] = mapped_column(JSONType())

    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    github_review_id: Mapped[int | None] = mapped_column(BigInteger)
    github_review_url: Mapped[str | None] = mapped_column(String(600))

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    pull_request: Mapped[PullRequest] = relationship(back_populates="review_runs")
    findings: Mapped[list[Finding]] = relationship(
        back_populates="review_run", cascade="all, delete-orphan"
    )
    comments: Mapped[list[ReviewComment]] = relationship(
        back_populates="review_run", cascade="all, delete-orphan"
    )
    llm_usages: Mapped[list[LLMUsage]] = relationship(
        back_populates="review_run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ReviewRun {self.id} {self.status}>"


class Finding(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "findings"
    __table_args__ = (
        Index("ix_findings_review_run", "review_run_id"),
        Index("ix_findings_severity", "severity"),
        Index("ix_findings_category", "category"),
        Index("ix_findings_status", "status"),
        Index("ix_findings_run_file", "review_run_id", "file_path"),
    )

    review_run_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    line_number: Mapped[int | None] = mapped_column(Integer)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)

    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text)
    code_snippet: Mapped[str | None] = mapped_column(Text)
    suggested_fix: Mapped[str | None] = mapped_column(Text)

    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    confidence_label: Mapped[str] = mapped_column(String(20), default="likely", nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="llm", nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(120))
    references: Mapped[list | None] = mapped_column(JSONType())
    related_symbols: Mapped[list | None] = mapped_column(JSONType())
    historical_context: Mapped[dict | None] = mapped_column(JSONType())

    status: Mapped[str] = mapped_column(
        String(20), default=FindingStatus.OPEN.value, nullable=False
    )
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    review_run: Mapped[ReviewRun] = relationship(back_populates="findings")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Finding {self.severity} {self.file_path}:{self.line_number}>"


class ReviewComment(UUIDMixin, TimestampMixin, Base):
    """A comment actually published to GitHub (summary or inline)."""

    __tablename__ = "review_comments"
    __table_args__ = (Index("ix_review_comments_run", "review_run_id"),)

    review_run_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False
    )
    finding_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("findings.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(20), default="inline", nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1000))
    line_number: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger)
    github_url: Mapped[str | None] = mapped_column(String(600))
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

    review_run: Mapped[ReviewRun] = relationship(back_populates="comments")


class LLMUsage(UUIDMixin, TimestampMixin, Base):
    """Per-call LLM cost/latency accounting (spec section 50)."""

    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_run", "review_run_id"),)

    review_run_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("review_runs.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    operation: Mapped[str] = mapped_column(String(60), default="generate_review", nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

    review_run: Mapped[ReviewRun] = relationship(back_populates="llm_usages")


class AuditLog(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_created_at", "created_at"),
    )

    user_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    context: Mapped[dict | None] = mapped_column(JSONType())
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))


class WebhookDelivery(UUIDMixin, TimestampMixin, Base):
    """Records processed webhook deliveries for idempotency + debugging."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("delivery_id", name="uq_webhook_delivery_id"),
        Index("ix_webhook_deliveries_event", "event"),
    )

    delivery_id: Mapped[str] = mapped_column(String(120), nullable=False)
    event: Mapped[str] = mapped_column(String(60), nullable=False)
    action: Mapped[str | None] = mapped_column(String(60))
    installation_id: Mapped[int | None] = mapped_column(BigInteger)
    repository_full_name: Mapped[str | None] = mapped_column(String(400))
    pr_number: Mapped[int | None] = mapped_column(Integer)
    head_sha: Mapped[str | None] = mapped_column(String(64))
    accepted: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    review_run_id: Mapped[str | None] = mapped_column(
        GUID(), ForeignKey("review_runs.id", ondelete="SET NULL")
    )
