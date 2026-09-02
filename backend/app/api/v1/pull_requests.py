"""Pull request endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.deps import (
    AccessibleInstallations,
    CurrentUser,
    DbSession,
    OwnedPullRequest,
    Pagination,
    load_repository,
)
from app.core.errors import GitHubError
from app.core.logging import get_logger
from app.integrations.github.factory import get_github_provider
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.review import ReviewRun
from app.models.user import GitHubInstallation
from app.schemas.common import Page
from app.schemas.pull_request import PullRequestSummary
from app.schemas.review import ReviewEnqueuedResponse, ReviewRunResponse
from app.services.queue import enqueue_review
from app.services.review_service import get_or_create_review_run, upsert_pull_request

logger = get_logger(__name__)
router = APIRouter(prefix="/pull-requests", tags=["pull-requests"])


class ImportPullRequestRequest(BaseModel):
    repository_id: str
    number: int = Field(ge=1)
    review: bool = Field(default=True)


def _latest_review_subquery():
    """Latest review run per pull request, resolved in SQL, not in Python."""
    return (
        select(
            ReviewRun.pull_request_id.label("pr_id"),
            func.max(ReviewRun.created_at).label("latest_created_at"),
        )
        .group_by(ReviewRun.pull_request_id)
        .subquery()
    )


@router.get("", response_model=Page[PullRequestSummary])
async def list_pull_requests(
    db: DbSession,
    installation_ids: AccessibleInstallations,
    pagination: Pagination,
    repository_id: str | None = Query(default=None),
    state: str | None = Query(default=None, pattern="^(open|closed|merged)$"),
    author: str | None = Query(default=None, max_length=200),
    search: str | None = Query(default=None, max_length=200),
) -> Page[PullRequestSummary]:
    if not installation_ids:
        return Page.build([], 0, pagination.page, pagination.page_size)

    conditions = [Repository.installation_id.in_(installation_ids)]
    if repository_id:
        conditions.append(PullRequest.repository_id == repository_id)
    if state:
        conditions.append(PullRequest.state == state)
    if author:
        conditions.append(func.lower(PullRequest.author) == author.lower())
    if search:
        pattern = f"%{search.lower()}%"
        conditions.append(
            or_(
                func.lower(PullRequest.title).like(pattern),
                func.lower(PullRequest.head_branch).like(pattern),
            )
        )

    base = select(PullRequest).join(Repository, Repository.id == PullRequest.repository_id)
    total = await db.scalar(
        select(func.count(PullRequest.id))
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(*conditions)
    )

    latest = _latest_review_subquery()
    rows = await db.execute(
        base.add_columns(
            Repository.full_name,
            ReviewRun.id,
            ReviewRun.status,
            ReviewRun.risk_score,
            ReviewRun.risk_band,
            ReviewRun.findings_count,
        )
        .outerjoin(latest, latest.c.pr_id == PullRequest.id)
        .outerjoin(
            ReviewRun,
            (ReviewRun.pull_request_id == PullRequest.id)
            & (ReviewRun.created_at == latest.c.latest_created_at),
        )
        .where(*conditions)
        .order_by(PullRequest.updated_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )

    items: list[PullRequestSummary] = []
    for pr, full_name, run_id, run_status, risk_score, risk_band, findings in rows.all():
        item = PullRequestSummary.model_validate(pr)
        item.repository_full_name = full_name
        item.latest_review_id = run_id
        item.latest_review_status = run_status
        item.latest_review_risk_score = risk_score
        item.latest_review_risk_band = risk_band
        item.findings_count = int(findings or 0)
        items.append(item)

    if items:
        counts = await db.execute(
            select(ReviewRun.pull_request_id, func.count(ReviewRun.id))
            .where(ReviewRun.pull_request_id.in_([item.id for item in items]))
            .group_by(ReviewRun.pull_request_id)
        )
        by_pr = {pr_id: int(count) for pr_id, count in counts.all()}
        for item in items:
            item.review_count = by_pr.get(item.id, 0)

    return Page.build(items, int(total or 0), pagination.page, pagination.page_size)


@router.get("/{pull_request_id}", response_model=PullRequestSummary)
async def get_pull_request(
    pull_request: OwnedPullRequest, db: DbSession
) -> PullRequestSummary:
    item = PullRequestSummary.model_validate(pull_request)
    item.repository_full_name = pull_request.repository.full_name
    latest = await db.scalar(
        select(ReviewRun)
        .where(ReviewRun.pull_request_id == pull_request.id)
        .order_by(ReviewRun.created_at.desc())
        .limit(1)
    )
    if latest is not None:
        item.latest_review_id = latest.id
        item.latest_review_status = latest.status
        item.latest_review_risk_score = latest.risk_score
        item.latest_review_risk_band = latest.risk_band
        item.findings_count = latest.findings_count
    item.review_count = int(
        await db.scalar(
            select(func.count(ReviewRun.id)).where(
                ReviewRun.pull_request_id == pull_request.id
            )
        )
        or 0
    )
    return item


@router.get("/{pull_request_id}/reviews", response_model=Page[ReviewRunResponse])
async def list_pull_request_reviews(
    pull_request: OwnedPullRequest, db: DbSession, pagination: Pagination
) -> Page[ReviewRunResponse]:
    total = await db.scalar(
        select(func.count(ReviewRun.id)).where(ReviewRun.pull_request_id == pull_request.id)
    )
    rows = await db.execute(
        select(ReviewRun)
        .where(ReviewRun.pull_request_id == pull_request.id)
        .order_by(ReviewRun.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    items = [ReviewRunResponse.model_validate(row) for row in rows.scalars().all()]
    return Page.build(items, int(total or 0), pagination.page, pagination.page_size)


@router.post("/import", response_model=ReviewEnqueuedResponse, status_code=status.HTTP_201_CREATED)
async def import_pull_request(
    payload: ImportPullRequestRequest,
    user: CurrentUser,
    db: DbSession,
    installation_ids: AccessibleInstallations,
) -> ReviewEnqueuedResponse:
    """Pull a PR's current state from GitHub and (optionally) queue a review."""
    repository = await load_repository(db, payload.repository_id, installation_ids)
    installation = await db.get(GitHubInstallation, repository.installation_id)
    if installation is None:
        raise GitHubError("Installation for this repository is missing", status_code=409)

    provider = get_github_provider()
    info = await provider.get_pull_request(
        installation.installation_id, repository.full_name, payload.number
    )
    pull_request = await upsert_pull_request(db, repository, info)

    if not payload.review:
        await db.commit()
        return ReviewEnqueuedResponse(
            review_run_id="",
            status="imported",
            created=True,
            task_id=None,
            detail=f"Imported {repository.full_name}#{payload.number} without queueing a review.",
        )

    review, created = await get_or_create_review_run(
        db, pull_request, trigger="manual", force=False
    )
    await db.commit()

    dispatch = enqueue_review(review.id) if created else None
    return ReviewEnqueuedResponse(
        review_run_id=review.id,
        status=review.status,
        created=created,
        task_id=dispatch.task_id if dispatch else None,
        detail=(
            dispatch.detail
            if dispatch
            else "A review already exists for this commit; returning the existing run."
        ),
    )
