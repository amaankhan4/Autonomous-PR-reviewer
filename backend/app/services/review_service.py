"""Database-side orchestration for reviews.

This module is the only place that turns GitHub objects into rows, and rows into
queued work. Keeping it separate from :mod:`app.services.review_engine` means the
engine stays a pure function of its inputs, and keeping it separate from the API
routers means the webhook handler, the manual "review now" button and the worker
all take the exact same path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import PullRequestState, ReviewStatus
from app.core.logging import get_logger
from app.integrations.github.base import (
    GitHubProvider,
    InstallationInfo,
    PullRequestInfo,
    RepositoryInfo,
)
from app.models.pull_request import PullRequest
from app.models.repository import Repository, RepositorySettings
from app.models.review import Finding, ReviewRun
from app.models.user import GitHubInstallation, InstallationMembership, User

logger = get_logger(__name__)

#: Statuses that mean "there is already work in flight for this commit".
ACTIVE_STATUSES = (
    ReviewStatus.QUEUED.value,
    ReviewStatus.PROCESSING.value,
    ReviewStatus.ANALYZING.value,
    ReviewStatus.REVIEWING.value,
    ReviewStatus.PUBLISHING.value,
)


# --------------------------------------------------------------- installations
async def upsert_installation(
    db: AsyncSession, info: InstallationInfo
) -> GitHubInstallation:
    result = await db.execute(
        select(GitHubInstallation).where(
            GitHubInstallation.installation_id == info.installation_id
        )
    )
    installation = result.scalar_one_or_none()
    if installation is None:
        installation = GitHubInstallation(installation_id=info.installation_id)
        db.add(installation)

    installation.account_login = info.account_login
    installation.account_id = info.account_id
    installation.account_type = info.account_type
    installation.avatar_url = info.avatar_url
    installation.permissions = dict(info.permissions or {})
    installation.is_active = True
    await db.flush()
    return installation


async def grant_membership(
    db: AsyncSession, user: User, installation: GitHubInstallation, role: str = "owner"
) -> InstallationMembership:
    result = await db.execute(
        select(InstallationMembership).where(
            InstallationMembership.user_id == user.id,
            InstallationMembership.installation_id == installation.id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        membership = InstallationMembership(
            user_id=user.id, installation_id=installation.id, role=role
        )
        db.add(membership)
        await db.flush()
    return membership


# ---------------------------------------------------------------- repositories
async def upsert_repository(
    db: AsyncSession, installation: GitHubInstallation, info: RepositoryInfo
) -> tuple[Repository, bool]:
    result = await db.execute(
        select(Repository)
        .options(selectinload(Repository.settings))
        .where(Repository.github_repo_id == info.github_repo_id)
    )
    repository = result.scalar_one_or_none()
    created = repository is None
    if repository is None:
        repository = Repository(github_repo_id=info.github_repo_id)
        db.add(repository)

    repository.installation_id = installation.id
    repository.owner = info.owner
    repository.name = info.name
    repository.full_name = info.full_name
    repository.description = info.description
    repository.default_branch = info.default_branch or "main"
    repository.private = info.private
    repository.language = info.language
    repository.html_url = info.html_url
    await db.flush()
    await ensure_repository_settings(db, repository)
    return repository, created


async def ensure_repository_settings(
    db: AsyncSession, repository: Repository
) -> RepositorySettings:
    result = await db.execute(
        select(RepositorySettings).where(RepositorySettings.repository_id == repository.id)
    )
    config = result.scalar_one_or_none()
    if config is None:
        config = RepositorySettings(repository_id=repository.id, **RepositorySettings.defaults())
        db.add(config)
        await db.flush()
    return config


async def sync_installation_repositories(
    db: AsyncSession, provider: GitHubProvider, installation: GitHubInstallation
) -> tuple[int, int]:
    """Mirror the installation's repository list into the database."""
    infos = await provider.list_installation_repositories(installation.installation_id)
    added = 0
    for info in infos:
        _, created = await upsert_repository(db, installation, info)
        added += int(created)
    await db.flush()
    logger.info(
        "installation.repositories_synced",
        installation_id=installation.installation_id,
        total=len(infos),
        added=added,
    )
    return len(infos), added


async def find_repository_by_full_name(
    db: AsyncSession, full_name: str
) -> Repository | None:
    result = await db.execute(
        select(Repository)
        .options(selectinload(Repository.settings), selectinload(Repository.installation))
        .where(Repository.full_name == full_name)
    )
    return result.scalar_one_or_none()


# --------------------------------------------------------------- pull requests
async def upsert_pull_request(
    db: AsyncSession, repository: Repository, info: PullRequestInfo
) -> PullRequest:
    result = await db.execute(
        select(PullRequest).where(
            PullRequest.repository_id == repository.id,
            PullRequest.github_pr_number == info.number,
        )
    )
    pull_request = result.scalar_one_or_none()
    if pull_request is None:
        pull_request = PullRequest(
            repository_id=repository.id,
            github_pr_number=info.number,
            opened_at=info.created_at or datetime.now(UTC),
        )
        db.add(pull_request)

    pull_request.github_pr_id = info.github_pr_id
    pull_request.title = info.title or f"Pull request #{info.number}"
    pull_request.body = info.body
    pull_request.author = info.author
    pull_request.author_avatar_url = info.author_avatar_url
    pull_request.base_branch = info.base_branch
    pull_request.head_branch = info.head_branch
    pull_request.base_sha = info.base_sha or ""
    pull_request.head_sha = info.head_sha or ""
    pull_request.state = PullRequestState.coerce(info.state).value
    pull_request.draft = info.draft
    pull_request.html_url = info.html_url
    pull_request.additions = info.additions
    pull_request.deletions = info.deletions
    pull_request.changed_files = info.changed_files
    if pull_request.state != PullRequestState.OPEN.value and pull_request.closed_at is None:
        pull_request.closed_at = datetime.now(UTC)
    await db.flush()
    return pull_request


# ----------------------------------------------------------------- review runs
async def get_or_create_review_run(
    db: AsyncSession,
    pull_request: PullRequest,
    *,
    commit_sha: str | None = None,
    trigger: str = "manual",
    force: bool = False,
) -> tuple[ReviewRun, bool]:
    """Idempotently obtain the review run for a PR at a commit.

    Repeat webhook deliveries for the same head SHA must not create duplicate
    work, so ``(pull_request_id, commit_sha)`` is looked up first. ``force``
    resets an existing terminal run back to ``queued`` instead of inserting a
    second row, which keeps the unique constraint intact and preserves history
    on the same identifier the UI is already polling.
    """
    sha = commit_sha or pull_request.head_sha
    result = await db.execute(
        select(ReviewRun).where(
            ReviewRun.pull_request_id == pull_request.id,
            ReviewRun.commit_sha == sha,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        if existing.status in ACTIVE_STATUSES and not force:
            return existing, False
        if not force:
            return existing, False
        await db.execute(
            Finding.__table__.delete().where(Finding.review_run_id == existing.id)
        )
        existing.status = ReviewStatus.QUEUED.value
        existing.stage = None
        existing.progress = 0
        existing.error_message = None
        existing.summary = None
        existing.findings_count = 0
        existing.suppressed_count = 0
        existing.published = False
        existing.started_at = None
        existing.completed_at = None
        existing.duration_ms = None
        existing.trigger = trigger
        await db.flush()
        return existing, True

    review = ReviewRun(
        pull_request_id=pull_request.id,
        commit_sha=sha,
        trigger=trigger,
        status=ReviewStatus.QUEUED.value,
    )
    db.add(review)
    await db.flush()
    return review, True


async def historical_findings(
    db: AsyncSession, repository_id: str, *, exclude_run_id: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    """Fingerprint history for the repository, used to add "seen before" context."""
    stmt = (
        select(
            Finding.fingerprint,
            func.count(Finding.id).label("occurrences"),
            func.max(Finding.created_at).label("last_seen"),
            func.max(Finding.status).label("status"),
        )
        .join(ReviewRun, ReviewRun.id == Finding.review_run_id)
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .where(PullRequest.repository_id == repository_id)
        .group_by(Finding.fingerprint)
        .order_by(func.max(Finding.created_at).desc())
        .limit(limit)
    )
    if exclude_run_id:
        stmt = stmt.where(Finding.review_run_id != exclude_run_id)

    rows = await db.execute(stmt)
    return [
        {
            "fingerprint": fingerprint,
            "occurrences": int(occurrences or 0),
            "last_seen": last_seen.isoformat() if last_seen else None,
            "status": status,
        }
        for fingerprint, occurrences, last_seen, status in rows.all()
    ]


async def latest_review_for_pull_request(
    db: AsyncSession, pull_request_id: str
) -> ReviewRun | None:
    result = await db.execute(
        select(ReviewRun)
        .where(ReviewRun.pull_request_id == pull_request_id)
        .order_by(ReviewRun.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
