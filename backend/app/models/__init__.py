"""SQLAlchemy models package.

Importing this module registers every mapper on ``Base.metadata`` which Alembic
autogeneration and ``create_all`` rely on.
"""

from app.models.base import Base, TimestampMixin, UUIDMixin, utcnow
from app.models.pull_request import PullRequest
from app.models.repository import (
    IndexedFile,
    Repository,
    RepositoryIndex,
    RepositorySettings,
)
from app.models.review import (
    AuditLog,
    Finding,
    LLMUsage,
    ReviewComment,
    ReviewRun,
    WebhookDelivery,
)
from app.models.user import (
    GitHubInstallation,
    InstallationMembership,
    OAuthState,
    User,
)

__all__ = [
    "AuditLog",
    "Base",
    "Finding",
    "GitHubInstallation",
    "IndexedFile",
    "InstallationMembership",
    "LLMUsage",
    "OAuthState",
    "PullRequest",
    "Repository",
    "RepositoryIndex",
    "RepositorySettings",
    "ReviewComment",
    "ReviewRun",
    "TimestampMixin",
    "UUIDMixin",
    "User",
    "WebhookDelivery",
    "utcnow",
]
