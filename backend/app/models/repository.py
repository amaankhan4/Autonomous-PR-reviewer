"""Repositories, per-repository review settings and index bookkeeping."""

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

from app.core.enums import IndexStatus
from app.db.types import GUID, JSONType
from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.pull_request import PullRequest
    from app.models.user import GitHubInstallation


class Repository(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("github_repo_id", name="uq_repositories_github_repo_id"),
        Index("ix_repositories_full_name", "full_name"),
        Index("ix_repositories_installation_active", "installation_id", "is_active"),
    )

    github_repo_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    installation_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("github_installations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    full_name: Mapped[str] = mapped_column(String(400), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    default_branch: Mapped[str] = mapped_column(String(200), default="main", nullable=False)
    private: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    language: Mapped[str | None] = mapped_column(String(80))
    html_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    installation: Mapped[GitHubInstallation] = relationship(back_populates="repositories")
    pull_requests: Mapped[list[PullRequest]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    settings: Mapped[RepositorySettings | None] = relationship(
        back_populates="repository",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    indexes: Mapped[list[RepositoryIndex]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Repository {self.full_name}>"


class RepositorySettings(UUIDMixin, TimestampMixin, Base):
    """Per-repository review configuration (spec section 39)."""

    __tablename__ = "repository_settings"

    repository_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    auto_review_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    review_on_open: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    review_on_synchronize: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    publish_to_github: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    min_severity: Mapped[str] = mapped_column(String(20), default="LOW", nullable=False)
    max_files_per_review: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    min_confidence: Mapped[float] = mapped_column(Float, default=0.55, nullable=False)
    excluded_paths: Mapped[list] = mapped_column(JSONType(), default=list, nullable=False)
    enabled_analyzers: Mapped[list] = mapped_column(JSONType(), default=list, nullable=False)
    llm_model: Mapped[str | None] = mapped_column(String(120))
    review_event: Mapped[str] = mapped_column(String(20), default="COMMENT", nullable=False)

    repository: Mapped[Repository] = relationship(back_populates="settings")

    @staticmethod
    def defaults() -> dict:
        from app.core.config import settings as app_settings

        return {
            "auto_review_enabled": True,
            "review_on_open": True,
            "review_on_synchronize": True,
            "publish_to_github": app_settings.PUBLISH_REVIEWS,
            "min_severity": app_settings.DEFAULT_MIN_SEVERITY,
            "max_files_per_review": app_settings.MAX_CHANGED_FILES,
            "min_confidence": app_settings.MIN_PUBLISH_CONFIDENCE,
            "excluded_paths": [
                "**/node_modules/**",
                "**/dist/**",
                "**/build/**",
                "**/*.lock",
                "**/*.min.js",
                "**/vendor/**",
            ],
            "enabled_analyzers": list(app_settings.ENABLED_ANALYZERS),
            "llm_model": app_settings.LLM_MODEL,
            "review_event": app_settings.DEFAULT_REVIEW_EVENT,
        }


class RepositoryIndex(UUIDMixin, TimestampMixin, Base):
    """One row per indexing run of a repository at a specific commit."""

    __tablename__ = "repository_indexes"
    __table_args__ = (
        Index("ix_repository_indexes_repo_commit", "repository_id", "commit_sha"),
        Index("ix_repository_indexes_status", "status"),
    )

    repository_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=IndexStatus.PENDING.value, nullable=False
    )
    files_indexed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    files_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunks_indexed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunks_deleted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    incremental: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    repository: Mapped[Repository] = relationship(back_populates="indexes")


class IndexedFile(UUIDMixin, TimestampMixin, Base):
    """Per-file index state, enabling incremental re-indexing (spec section 21)."""

    __tablename__ = "indexed_files"
    __table_args__ = (
        UniqueConstraint("repository_id", "file_path", name="uq_indexed_file_repo_path"),
        Index("ix_indexed_files_repo", "repository_id"),
    )

    repository_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(40), default="unknown", nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
