"""Review run endpoints: list, detail, trigger, progress, diff, cancel."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import (
    AccessibleInstallations,
    CurrentUser,
    DbSession,
    OwnedReview,
    Pagination,
    load_pull_request,
    load_repository,
    load_review_run,
)
from app.core.enums import AuditAction, ReviewStatus
from app.core.errors import ConflictError, GitHubError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.integrations.github.factory import get_github_provider
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.review import Finding, ReviewRun
from app.models.user import GitHubInstallation
from app.schemas.common import Page
from app.schemas.review import (
    FindingResponse,
    LLMUsageResponse,
    ReviewCommentResponse,
    ReviewDiffFile,
    ReviewDiffResponse,
    ReviewEnqueuedResponse,
    ReviewProgressResponse,
    ReviewRunDetail,
    ReviewRunSummary,
    ReviewTriggerRequest,
)
from app.services.audit import record_audit
from app.services.queue import enqueue_review
from app.services.review_service import ACTIVE_STATUSES, get_or_create_review_run

logger = get_logger(__name__)
router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("", response_model=Page[ReviewRunSummary])
async def list_reviews(
    db: DbSession,
    installation_ids: AccessibleInstallations,
    pagination: Pagination,
    repository_id: str | None = Query(default=None),
    pull_request_id: str | None = Query(default=None),
    review_status: str | None = Query(default=None, alias="status", max_length=20),
    risk_band: str | None = Query(default=None, max_length=20),
) -> Page[ReviewRunSummary]:
    if not installation_ids:
        return Page.build([], 0, pagination.page, pagination.page_size)

    conditions = [Repository.installation_id.in_(installation_ids)]
    if repository_id:
        conditions.append(PullRequest.repository_id == repository_id)
    if pull_request_id:
        conditions.append(ReviewRun.pull_request_id == pull_request_id)
    if review_status:
        conditions.append(ReviewRun.status == review_status)
    if risk_band:
        conditions.append(ReviewRun.risk_band == risk_band)

    joined = (
        select(ReviewRun)
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
    )
    total = await db.scalar(
        select(func.count(ReviewRun.id))
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(*conditions)
    )
    rows = await db.execute(
        joined.add_columns(
            Repository.id,
            Repository.full_name,
            PullRequest.github_pr_number,
            PullRequest.title,
            PullRequest.author,
        )
        .where(*conditions)
        .order_by(ReviewRun.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )

    items: list[ReviewRunSummary] = []
    for run, repo_id, full_name, number, title, author in rows.all():
        item = ReviewRunSummary.model_validate(run)
        item.repository_id = repo_id
        item.repository_full_name = full_name
        item.pull_request_number = number
        item.pull_request_title = title
        item.pull_request_author = author
        items.append(item)

    total_int = int(total or 0)
    return Page.build(items, total_int, pagination.page, pagination.page_size)


@router.post("", response_model=ReviewEnqueuedResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_review(
    payload: ReviewTriggerRequest,
    user: CurrentUser,
    db: DbSession,
    installation_ids: AccessibleInstallations,
) -> ReviewEnqueuedResponse:
    """Queue a review for a tracked pull request.

    Accepts either a ``pull_request_id`` or a ``repository_id`` + ``pr_number``
    pair; the latter also refreshes the PR from GitHub first so a stale head SHA
    cannot be reviewed by mistake.
    """
    if payload.pull_request_id:
        pull_request = await load_pull_request(db, payload.pull_request_id, installation_ids)
    elif payload.repository_id and payload.pr_number:
        repository = await load_repository(db, payload.repository_id, installation_ids)
        result = await db.execute(
            select(PullRequest).where(
                PullRequest.repository_id == repository.id,
                PullRequest.github_pr_number == payload.pr_number,
            )
        )
        pull_request = result.scalar_one_or_none()
        if pull_request is None:
            raise NotFoundError(
                "That pull request is not tracked yet. Import it first.",
                code="PULL_REQUEST_NOT_TRACKED",
            )
    else:
        raise ValidationError(
            "Provide either pull_request_id, or repository_id together with pr_number",
            code="REVIEW_TARGET_REQUIRED",
        )

    review, created = await get_or_create_review_run(
        db, pull_request, trigger="manual", force=payload.force
    )
    if not created and review.status in ACTIVE_STATUSES:
        await db.commit()
        return ReviewEnqueuedResponse(
            review_run_id=review.id,
            status=review.status,
            created=False,
            task_id=None,
            detail="A review for this commit is already in progress.",
        )

    await record_audit(
        db,
        AuditAction.REVIEW_ENQUEUED,
        user_id=user.id,
        entity_type="review_run",
        entity_id=review.id,
        context={"trigger": "manual", "force": payload.force},
    )
    await db.commit()

    if not created and not payload.force:
        return ReviewEnqueuedResponse(
            review_run_id=review.id,
            status=review.status,
            created=False,
            task_id=None,
            detail=(
                "This commit has already been reviewed. Pass force=true to run it again."
            ),
        )

    dispatch = enqueue_review(review.id)
    return ReviewEnqueuedResponse(
        review_run_id=review.id,
        status=ReviewStatus.QUEUED.value,
        created=True,
        task_id=dispatch.task_id,
        detail=dispatch.detail,
    )


@router.get("/{review_id}", response_model=ReviewRunDetail)
async def get_review(
    review_id: str, db: DbSession, installation_ids: AccessibleInstallations
) -> ReviewRunDetail:
    review = await load_review_run(db, review_id, installation_ids, with_findings=True)
    detail = ReviewRunDetail.model_validate(review)
    pull_request = review.pull_request
    detail.pull_request_number = pull_request.github_pr_number
    detail.pull_request_title = pull_request.title
    detail.pull_request_author = pull_request.author
    detail.repository_id = pull_request.repository.id
    detail.repository_full_name = pull_request.repository.full_name

    severity_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    findings = sorted(
        review.findings,
        key=lambda f: (severity_rank.get(f.severity, 9), -f.confidence, f.file_path),
    )
    detail.findings = [FindingResponse.model_validate(item) for item in findings]
    detail.comments = [ReviewCommentResponse.model_validate(item) for item in review.comments]
    detail.llm_usage = [LLMUsageResponse.model_validate(item) for item in review.llm_usages]
    return detail


@router.get("/{review_id}/progress", response_model=ReviewProgressResponse)
async def get_review_progress(review: OwnedReview) -> ReviewProgressResponse:
    return ReviewProgressResponse(
        review_run_id=review.id,
        status=review.status,
        stage=review.stage,
        progress=review.progress,
        findings_count=review.findings_count,
        error_message=review.error_message,
        updated_at=review.updated_at,
    )


@router.get("/{review_id}/findings", response_model=list[FindingResponse])
async def list_review_findings(
    review_id: str,
    db: DbSession,
    installation_ids: AccessibleInstallations,
    severity: str | None = Query(default=None, max_length=20),
    category: str | None = Query(default=None, max_length=40),
) -> list[FindingResponse]:
    await load_review_run(db, review_id, installation_ids)
    conditions = [Finding.review_run_id == review_id]
    if severity:
        conditions.append(Finding.severity == severity.upper())
    if category:
        conditions.append(Finding.category == category.lower())
    rows = await db.execute(
        select(Finding).where(*conditions).order_by(Finding.file_path, Finding.line_number)
    )
    return [FindingResponse.model_validate(row) for row in rows.scalars().all()]


@router.get("/{review_id}/diff", response_model=ReviewDiffResponse)
async def get_review_diff(
    review_id: str, db: DbSession, installation_ids: AccessibleInstallations
) -> ReviewDiffResponse:
    """The reviewed diff, with findings attached to the file they belong to.

    This is what powers the side-by-side code viewer: the frontend never has to
    guess which finding belongs to which patch.
    """
    review = await load_review_run(db, review_id, installation_ids, with_findings=True)
    repository = review.pull_request.repository
    installation = await db.get(GitHubInstallation, repository.installation_id)
    if installation is None:
        raise GitHubError("Installation for this repository is missing", status_code=409)

    provider = get_github_provider()
    try:
        changed = await provider.get_changed_files(
            installation.installation_id,
            repository.full_name,
            review.pull_request.github_pr_number,
        )
    except Exception as exc:
        raise GitHubError(f"Could not load the diff for this review: {exc}") from exc

    from app.analyzers.base import detect_language

    by_file: dict[str, list[FindingResponse]] = {}
    for finding in review.findings:
        by_file.setdefault(finding.file_path, []).append(
            FindingResponse.model_validate(finding)
        )

    files = [
        ReviewDiffFile(
            filename=entry.filename,
            status=entry.status,
            additions=entry.additions,
            deletions=entry.deletions,
            changes=entry.changes,
            patch=entry.patch,
            language=str(detect_language(entry.filename)),
            findings=by_file.get(entry.filename, []),
        )
        for entry in changed
    ]
    return ReviewDiffResponse(
        review_run_id=review.id,
        commit_sha=review.commit_sha,
        files=files,
        truncated=len(files) < len(changed),
    )


@router.post("/{review_id}/cancel", response_model=ReviewProgressResponse)
async def cancel_review(
    review: OwnedReview, user: CurrentUser, db: DbSession
) -> ReviewProgressResponse:
    if ReviewStatus(review.status).is_terminal:
        raise ConflictError(
            "This review has already finished and cannot be cancelled",
            code="REVIEW_NOT_CANCELLABLE",
        )
    review.status = ReviewStatus.CANCELLED.value
    review.stage = "cancelled"
    review.completed_at = datetime.now(UTC)
    await record_audit(
        db,
        AuditAction.REVIEW_FAILED,
        user_id=user.id,
        entity_type="review_run",
        entity_id=review.id,
        context={"cancelled": True},
    )
    await db.commit()
    await db.refresh(review)
    return ReviewProgressResponse(
        review_run_id=review.id,
        status=review.status,
        stage=review.stage,
        progress=review.progress,
        findings_count=review.findings_count,
        error_message=review.error_message,
        updated_at=review.updated_at,
    )


@router.get("/{review_id}/comments", response_model=list[ReviewCommentResponse])
async def list_review_comments(
    review_id: str, db: DbSession, installation_ids: AccessibleInstallations
) -> list[ReviewCommentResponse]:
    review = await load_review_run(db, review_id, installation_ids)
    result = await db.execute(
        select(ReviewRun)
        .options(selectinload(ReviewRun.comments))
        .where(ReviewRun.id == review.id)
    )
    loaded = result.scalar_one()
    return [ReviewCommentResponse.model_validate(item) for item in loaded.comments]
