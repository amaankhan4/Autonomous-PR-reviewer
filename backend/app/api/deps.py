"""Shared FastAPI dependencies: authentication, authorisation and pagination.

Authorisation rule of the system
--------------------------------
A user may only see data that hangs off a GitHub App installation they are a
member of (:class:`~app.models.user.InstallationMembership`). Every repository,
pull request, review and finding lookup is resolved *through* that membership
join, so an attacker who guesses a UUID still gets a 404. Identifiers supplied
by the client are never trusted to imply ownership.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import (
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    PullRequestNotFound,
    RepositoryNotFound,
    ReviewNotFound,
)
from app.core.security import decode_token
from app.db.session import get_db
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.review import ReviewRun
from app.models.user import GitHubInstallation, InstallationMembership, User

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("Missing bearer token", code="NOT_AUTHENTICATED")
    return token.strip()


async def get_current_user(request: Request, db: DbSession) -> User:
    payload = decode_token(_bearer_token(request), expected_type="access")
    user_id = str(payload.get("sub") or "")
    if not user_id:
        raise AuthenticationError("Token is missing a subject", code="TOKEN_INVALID")

    user = await db.get(User, user_id)
    if user is None:
        raise AuthenticationError("User no longer exists", code="USER_NOT_FOUND")
    if not user.is_active:
        raise AuthorizationError("This account is disabled", code="USER_DISABLED")
    return user


async def get_optional_user(request: Request, db: DbSession) -> User | None:
    """Best-effort authentication used by endpoints that degrade gracefully."""
    try:
        return await get_current_user(request, db)
    except (AuthenticationError, AuthorizationError):
        return None


CurrentUser = Annotated[User, Depends(get_current_user)]


async def user_installation_ids(db: AsyncSession, user: User) -> list[str]:
    if user.is_superuser:
        rows = await db.execute(select(GitHubInstallation.id))
        return [str(row) for row in rows.scalars().all()]
    rows = await db.execute(
        select(InstallationMembership.installation_id).where(
            InstallationMembership.user_id == user.id
        )
    )
    return [str(row) for row in rows.scalars().all()]


async def accessible_installation_ids(db: DbSession, user: CurrentUser) -> list[str]:
    return await user_installation_ids(db, user)


AccessibleInstallations = Annotated[list[str], Depends(accessible_installation_ids)]


async def load_repository(
    db: AsyncSession, repository_id: str, installation_ids: list[str]
) -> Repository:
    if not installation_ids:
        raise RepositoryNotFound()
    result = await db.execute(
        select(Repository)
        .options(selectinload(Repository.settings), selectinload(Repository.installation))
        .where(
            Repository.id == repository_id,
            Repository.installation_id.in_(installation_ids),
        )
    )
    repository = result.scalar_one_or_none()
    if repository is None:
        raise RepositoryNotFound()
    return repository


async def get_repository_dep(
    repository_id: str,
    db: DbSession,
    installation_ids: AccessibleInstallations,
) -> Repository:
    return await load_repository(db, repository_id, installation_ids)


OwnedRepository = Annotated[Repository, Depends(get_repository_dep)]


async def load_pull_request(
    db: AsyncSession, pull_request_id: str, installation_ids: list[str]
) -> PullRequest:
    if not installation_ids:
        raise PullRequestNotFound()
    result = await db.execute(
        select(PullRequest)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .options(selectinload(PullRequest.repository))
        .where(
            PullRequest.id == pull_request_id,
            Repository.installation_id.in_(installation_ids),
        )
    )
    pull_request = result.scalar_one_or_none()
    if pull_request is None:
        raise PullRequestNotFound()
    return pull_request


async def get_pull_request_dep(
    pull_request_id: str,
    db: DbSession,
    installation_ids: AccessibleInstallations,
) -> PullRequest:
    return await load_pull_request(db, pull_request_id, installation_ids)


OwnedPullRequest = Annotated[PullRequest, Depends(get_pull_request_dep)]


async def load_review_run(
    db: AsyncSession,
    review_id: str,
    installation_ids: list[str],
    *,
    with_findings: bool = False,
) -> ReviewRun:
    if not installation_ids:
        raise ReviewNotFound()
    stmt = (
        select(ReviewRun)
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(
            ReviewRun.id == review_id,
            Repository.installation_id.in_(installation_ids),
        )
    )
    options: list[Any] = [
        selectinload(ReviewRun.pull_request).selectinload(PullRequest.repository)
    ]
    if with_findings:
        options += [
            selectinload(ReviewRun.findings),
            selectinload(ReviewRun.comments),
            selectinload(ReviewRun.llm_usages),
        ]
    result = await db.execute(stmt.options(*options))
    review = result.scalar_one_or_none()
    if review is None:
        raise ReviewNotFound()
    return review


async def get_review_dep(
    review_id: str,
    db: DbSession,
    installation_ids: AccessibleInstallations,
) -> ReviewRun:
    return await load_review_run(db, review_id, installation_ids)


OwnedReview = Annotated[ReviewRun, Depends(get_review_dep)]


async def require_installation(
    db: AsyncSession, installation_id: str, installation_ids: list[str]
) -> GitHubInstallation:
    if installation_id not in installation_ids:
        raise NotFoundError("Installation not found", code="INSTALLATION_NOT_FOUND")
    installation = await db.get(GitHubInstallation, installation_id)
    if installation is None:
        raise NotFoundError("Installation not found", code="INSTALLATION_NOT_FOUND")
    return installation


class PaginationParams:
    """Offset pagination shared by every list endpoint."""

    def __init__(
        self,
        page: int = Query(default=1, ge=1, le=10_000),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


Pagination = Annotated[PaginationParams, Depends(PaginationParams)]


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None
