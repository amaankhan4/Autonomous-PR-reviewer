"""GitHub provider abstraction.

Every GitHub interaction in the application goes through :class:`GitHubProvider`.
Concentrating the API surface here keeps credentials in one place, makes the
whole review pipeline testable without network access, and lets the product run
in demo mode with :class:`~app.integrations.github.mock_provider.MockGitHubProvider`.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class RepositoryInfo:
    github_repo_id: int
    owner: str
    name: str
    full_name: str
    default_branch: str = "main"
    private: bool = True
    description: str | None = None
    language: str | None = None
    html_url: str | None = None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> RepositoryInfo:
        owner = (payload.get("owner") or {}).get("login") or payload.get("full_name", "/").split("/")[0]
        return cls(
            github_repo_id=int(payload["id"]),
            owner=owner,
            name=payload["name"],
            full_name=payload.get("full_name") or f"{owner}/{payload['name']}",
            default_branch=payload.get("default_branch") or "main",
            private=bool(payload.get("private", True)),
            description=payload.get("description"),
            language=payload.get("language"),
            html_url=payload.get("html_url"),
        )


@dataclass(slots=True)
class PullRequestInfo:
    number: int
    github_pr_id: int | None
    title: str
    body: str | None
    author: str
    author_avatar_url: str | None
    base_branch: str
    head_branch: str
    base_sha: str
    head_sha: str
    state: str = "open"
    draft: bool = False
    html_url: str | None = None
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    created_at: datetime | None = None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> PullRequestInfo:
        user = payload.get("user") or {}
        base = payload.get("base") or {}
        head = payload.get("head") or {}
        merged = bool(payload.get("merged_at"))
        state = "merged" if merged else str(payload.get("state") or "open")
        return cls(
            number=int(payload["number"]),
            github_pr_id=payload.get("id"),
            title=payload.get("title") or "",
            body=payload.get("body"),
            author=user.get("login") or "unknown",
            author_avatar_url=user.get("avatar_url"),
            base_branch=base.get("ref") or "main",
            head_branch=head.get("ref") or "",
            base_sha=base.get("sha") or "",
            head_sha=head.get("sha") or "",
            state=state,
            draft=bool(payload.get("draft", False)),
            html_url=payload.get("html_url"),
            additions=int(payload.get("additions") or 0),
            deletions=int(payload.get("deletions") or 0),
            changed_files=int(payload.get("changed_files") or 0),
        )


@dataclass(slots=True)
class ChangedFile:
    filename: str
    status: str
    additions: int
    deletions: int
    changes: int
    patch: str | None = None
    previous_filename: str | None = None
    sha: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "status": self.status,
            "additions": self.additions,
            "deletions": self.deletions,
            "changes": self.changes,
            "patch": self.patch,
            "previous_filename": self.previous_filename,
        }


@dataclass(slots=True)
class InstallationInfo:
    installation_id: int
    account_login: str
    account_id: int | None = None
    account_type: str = "User"
    avatar_url: str | None = None
    permissions: dict[str, str] = field(default_factory=dict)
    repositories: list[RepositoryInfo] = field(default_factory=list)


@dataclass(slots=True)
class InlineComment:
    """An inline review comment anchored to a line of the diff."""

    path: str
    body: str
    line: int | None = None
    side: str = "RIGHT"
    position: int | None = None

    def to_api(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"path": self.path, "body": self.body}
        if self.line is not None:
            payload["line"] = self.line
            payload["side"] = self.side
        elif self.position is not None:
            payload["position"] = self.position
        return payload


@dataclass(slots=True)
class PublishedReview:
    review_id: int | None
    html_url: str | None
    submitted_comments: int
    rejected_comments: list[dict[str, Any]] = field(default_factory=list)


class GitHubProvider(abc.ABC):
    """Interface implemented by the real GitHub App client and the mock."""

    #: Human-readable provider name surfaced in the API/UI.
    name: str = "github"
    #: True when the provider returns fabricated data (demo mode).
    is_mock: bool = False

    @abc.abstractmethod
    async def get_installation(self, installation_id: int) -> InstallationInfo: ...

    @abc.abstractmethod
    async def list_installation_repositories(
        self, installation_id: int
    ) -> list[RepositoryInfo]: ...

    @abc.abstractmethod
    async def get_repository(self, installation_id: int, full_name: str) -> RepositoryInfo: ...

    @abc.abstractmethod
    async def get_pull_request(
        self, installation_id: int, full_name: str, number: int
    ) -> PullRequestInfo: ...

    @abc.abstractmethod
    async def get_pull_request_diff(
        self, installation_id: int, full_name: str, number: int
    ) -> str: ...

    @abc.abstractmethod
    async def get_changed_files(
        self, installation_id: int, full_name: str, number: int
    ) -> list[ChangedFile]: ...

    @abc.abstractmethod
    async def get_file(
        self, installation_id: int, full_name: str, path: str, ref: str
    ) -> str | None: ...

    @abc.abstractmethod
    async def list_files(
        self, installation_id: int, full_name: str, ref: str
    ) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    async def create_review(
        self,
        installation_id: int,
        full_name: str,
        number: int,
        *,
        commit_sha: str,
        body: str,
        event: str = "COMMENT",
        comments: list[InlineComment] | None = None,
    ) -> PublishedReview: ...

    @abc.abstractmethod
    async def create_inline_comment(
        self,
        installation_id: int,
        full_name: str,
        number: int,
        *,
        commit_sha: str,
        comment: InlineComment,
    ) -> dict[str, Any]: ...

    async def close(self) -> None:  # pragma: no cover - default no-op
        return None
