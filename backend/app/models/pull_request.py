"""Pull requests tracked by the reviewer."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import PullRequestState
from app.db.types import GUID
from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.repository import Repository
    from app.models.review import ReviewRun


class PullRequest(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        UniqueConstraint("repository_id", "github_pr_number", name="uq_pr_repo_number"),
        Index("ix_pull_requests_repo_number", "repository_id", "github_pr_number"),
        Index("ix_pull_requests_state", "state"),
        Index("ix_pull_requests_head_sha", "head_sha"),
    )

    repository_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    github_pr_id: Mapped[int | None] = mapped_column(BigInteger)
    github_pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(600), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(200), nullable=False)
    author_avatar_url: Mapped[str | None] = mapped_column(String(500))
    base_branch: Mapped[str | None] = mapped_column(String(300))
    head_branch: Mapped[str | None] = mapped_column(String(300))
    base_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(20), default=PullRequestState.OPEN.value, nullable=False
    )
    draft: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    html_url: Mapped[str | None] = mapped_column(String(600))
    additions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deletions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    changed_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    repository: Mapped[Repository] = relationship(back_populates="pull_requests")
    review_runs: Mapped[list[ReviewRun]] = relationship(
        back_populates="pull_request",
        cascade="all, delete-orphan",
        order_by="ReviewRun.created_at.desc()",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PullRequest #{self.github_pr_number}>"
