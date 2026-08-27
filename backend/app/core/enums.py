"""Domain enumerations shared by models, schemas and services."""

from __future__ import annotations

from enum import StrEnum


class Severity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def weight(self) -> int:
        return _SEVERITY_WEIGHT[self]

    @classmethod
    def coerce(cls, value: object, default: Severity = None) -> Severity:  # type: ignore[assignment]
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            candidate = value.strip().upper()
            aliases = {
                "WARNING": cls.MEDIUM,
                "WARN": cls.MEDIUM,
                "ERROR": cls.HIGH,
                "NOTE": cls.INFO,
                "MODERATE": cls.MEDIUM,
                "BLOCKER": cls.CRITICAL,
            }
            if candidate in cls.__members__:
                return cls[candidate]
            if candidate in aliases:
                return aliases[candidate]
        return default if default is not None else cls.MEDIUM


_SEVERITY_WEIGHT: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}

SEVERITY_ORDER: list[Severity] = [
    Severity.CRITICAL,
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
    Severity.INFO,
]


class Category(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    PERFORMANCE = "performance"
    RELIABILITY = "reliability"
    MAINTAINABILITY = "maintainability"
    TESTING = "testing"
    ARCHITECTURE = "architecture"
    STYLE = "style"

    @classmethod
    def coerce(cls, value: object) -> Category:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            candidate = value.strip().lower().replace(" ", "_")
            aliases = {
                "bug": cls.CORRECTNESS,
                "logic": cls.CORRECTNESS,
                "vulnerability": cls.SECURITY,
                "perf": cls.PERFORMANCE,
                "robustness": cls.RELIABILITY,
                "readability": cls.MAINTAINABILITY,
                "tests": cls.TESTING,
                "test": cls.TESTING,
                "design": cls.ARCHITECTURE,
                "formatting": cls.STYLE,
            }
            for member in cls:
                if member.value == candidate:
                    return member
            if candidate in aliases:
                return aliases[candidate]
        return cls.MAINTAINABILITY


class FindingStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    RESOLVED = "resolved"


class Confidence(StrEnum):
    """How certain the reviewer is that a finding is real."""

    CONFIRMED = "confirmed"
    LIKELY = "likely"
    UNCERTAIN = "uncertain"

    @classmethod
    def from_score(cls, score: float) -> Confidence:
        if score >= 0.85:
            return cls.CONFIRMED
        if score >= 0.6:
            return cls.LIKELY
        return cls.UNCERTAIN


class ReviewStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    ANALYZING = "analyzing"
    REVIEWING = "reviewing"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {ReviewStatus.COMPLETED, ReviewStatus.FAILED, ReviewStatus.CANCELLED}


class IndexStatus(StrEnum):
    PENDING = "pending"
    INDEXING = "indexing"
    COMPLETED = "completed"
    FAILED = "failed"
    STALE = "stale"


class PullRequestState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"

    @classmethod
    def coerce(cls, value: object) -> PullRequestState:
        if isinstance(value, cls):
            return value
        candidate = str(value or "").strip().lower()
        for member in cls:
            if member.value == candidate:
                return member
        return cls.OPEN


class RiskBand(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def from_score(cls, score: float) -> RiskBand:
        if score <= 20:
            return cls.LOW
        if score <= 40:
            return cls.MODERATE
        if score <= 60:
            return cls.ELEVATED
        if score <= 80:
            return cls.HIGH
        return cls.CRITICAL


class Language(StrEnum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    JSON = "json"
    YAML = "yaml"
    MARKDOWN = "markdown"
    SQL = "sql"
    SHELL = "shell"
    HTML = "html"
    CSS = "css"
    GO = "go"
    JAVA = "java"
    RUBY = "ruby"
    UNKNOWN = "unknown"


class AuditAction(StrEnum):
    USER_REGISTERED = "user.registered"
    USER_LOGIN = "user.login"
    USER_LOGOUT = "user.logout"
    INSTALLATION_LINKED = "installation.linked"
    INSTALLATION_REMOVED = "installation.removed"
    REPOSITORY_ACTIVATED = "repository.activated"
    REPOSITORY_DEACTIVATED = "repository.deactivated"
    REPOSITORY_REINDEXED = "repository.reindexed"
    REVIEW_ENQUEUED = "review.enqueued"
    REVIEW_COMPLETED = "review.completed"
    REVIEW_FAILED = "review.failed"
    REVIEW_PUBLISHED = "review.published"
    FINDING_STATUS_CHANGED = "finding.status_changed"
    SETTINGS_UPDATED = "settings.updated"
    WEBHOOK_RECEIVED = "webhook.received"
    WEBHOOK_REJECTED = "webhook.rejected"
