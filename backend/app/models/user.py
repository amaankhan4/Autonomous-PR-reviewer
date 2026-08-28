"""Application users and their link to GitHub App installations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import GUID, JSONType
from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.repository import Repository


class User(UUIDMixin, TimestampMixin, Base):
    """An application user.

    Application authentication (email + password -> JWT) is intentionally kept
    separate from GitHub App installation authorisation.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    github_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    github_login: Mapped[str | None] = mapped_column(String(120), index=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    memberships: Mapped[list[InstallationMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<User {self.username}>"


class GitHubInstallation(UUIDMixin, TimestampMixin, Base):
    """A GitHub App installation on a user/organisation account."""

    __tablename__ = "github_installations"
    __table_args__ = (Index("ix_github_installations_account", "account_login"),)

    installation_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True, index=True
    )
    account_login: Mapped[str] = mapped_column(String(200), nullable=False)
    account_id: Mapped[int | None] = mapped_column(BigInteger)
    account_type: Mapped[str] = mapped_column(String(40), default="User", nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(40))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    permissions: Mapped[dict | None] = mapped_column(JSONType())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    suspended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    repositories: Mapped[list[Repository]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )
    memberships: Mapped[list[InstallationMembership]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<GitHubInstallation {self.account_login}#{self.installation_id}>"


class InstallationMembership(UUIDMixin, TimestampMixin, Base):
    """Authorisation edge: which application users may see which installation.

    Repository access is always resolved through this table server-side; a
    user id supplied by the frontend is never trusted.
    """

    __tablename__ = "installation_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "installation_id", name="uq_membership_user_installation"),
    )

    user_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    installation_id: Mapped[str] = mapped_column(
        GUID(),
        ForeignKey("github_installations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(30), default="owner", nullable=False)

    user: Mapped[User] = relationship(back_populates="memberships")
    installation: Mapped[GitHubInstallation] = relationship(back_populates="memberships")


class OAuthState(UUIDMixin, TimestampMixin, Base):
    """Short-lived CSRF state for the GitHub App connect flow."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    user_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    redirect_to: Mapped[str | None] = mapped_column(String(500))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
