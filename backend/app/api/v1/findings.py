"""Finding endpoints: cross-repository triage."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request
from sqlalchemy import case, func, or_, select

from app.api.deps import (
    AccessibleInstallations,
    CurrentUser,
    DbSession,
    Pagination,
    client_ip,
)
from app.core.enums import AuditAction, FindingStatus
from app.core.errors import FindingNotFound
from app.core.logging import get_logger
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.review import Finding, ReviewRun
from app.schemas.common import Page
from app.schemas.review import FindingResponse, FindingStatusUpdate, FindingWithContext
from app.services.audit import record_audit

logger = get_logger(__name__)
router = APIRouter(prefix="/findings", tags=["findings"])

SEVERITY_ORDER_SQL = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


def _scoped_query(installation_ids: list[str]):
    return (
        select(Finding)
        .join(ReviewRun, ReviewRun.id == Finding.review_run_id)
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(Repository.installation_id.in_(installation_ids))
    )


@router.get("", response_model=Page[FindingWithContext])
async def list_findings(
    db: DbSession,
    installation_ids: AccessibleInstallations,
    pagination: Pagination,
    repository_id: str | None = Query(default=None),
    review_run_id: str | None = Query(default=None),
    severity: str | None = Query(default=None, max_length=20),
    category: str | None = Query(default=None, max_length=40),
    finding_status: str | None = Query(default=None, alias="status", max_length=20),
    source: str | None = Query(default=None, max_length=40),
    search: str | None = Query(default=None, max_length=200),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
) -> Page[FindingWithContext]:
    if not installation_ids:
        return Page.build([], 0, pagination.page, pagination.page_size)

    conditions = []
    if repository_id:
        conditions.append(PullRequest.repository_id == repository_id)
    if review_run_id:
        conditions.append(Finding.review_run_id == review_run_id)
    if severity:
        conditions.append(Finding.severity == severity.upper())
    if category:
        conditions.append(Finding.category == category.lower())
    if finding_status:
        conditions.append(Finding.status == finding_status.lower())
    if source:
        conditions.append(Finding.source == source.lower())
    if min_confidence is not None:
        conditions.append(Finding.confidence >= min_confidence)
    if search:
        pattern = f"%{search.lower()}%"
        conditions.append(
            or_(
                func.lower(Finding.title).like(pattern),
                func.lower(Finding.file_path).like(pattern),
            )
        )

    total = await db.scalar(
        select(func.count(Finding.id))
        .join(ReviewRun, ReviewRun.id == Finding.review_run_id)
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(Repository.installation_id.in_(installation_ids), *conditions)
    )

    severity_rank = case(
        {
            "CRITICAL": 0,
            "HIGH": 1,
            "MEDIUM": 2,
            "LOW": 3,
            "INFO": 4,
        },
        value=Finding.severity,
        else_=9,
    )
    rows = await db.execute(
        _scoped_query(installation_ids)
        .add_columns(
            Repository.full_name,
            PullRequest.github_pr_number,
            ReviewRun.commit_sha,
        )
        .where(*conditions)
        .order_by(severity_rank.asc(), Finding.confidence.desc(), Finding.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )

    items: list[FindingWithContext] = []
    for finding, full_name, pr_number, commit_sha in rows.all():
        item = FindingWithContext.model_validate(finding)
        item.repository_full_name = full_name
        item.pull_request_number = pr_number
        item.commit_sha = commit_sha
        items.append(item)
    return Page.build(items, int(total or 0), pagination.page, pagination.page_size)


@router.get("/{finding_id}", response_model=FindingWithContext)
async def get_finding(
    finding_id: str, db: DbSession, installation_ids: AccessibleInstallations
) -> FindingWithContext:
    if not installation_ids:
        raise FindingNotFound()
    result = await db.execute(
        _scoped_query(installation_ids)
        .add_columns(Repository.full_name, PullRequest.github_pr_number, ReviewRun.commit_sha)
        .where(Finding.id == finding_id)
    )
    row = result.first()
    if row is None:
        raise FindingNotFound()
    finding, full_name, pr_number, commit_sha = row
    item = FindingWithContext.model_validate(finding)
    item.repository_full_name = full_name
    item.pull_request_number = pr_number
    item.commit_sha = commit_sha
    return item


@router.patch("/{finding_id}/status", response_model=FindingResponse)
async def update_finding_status(
    finding_id: str,
    payload: FindingStatusUpdate,
    user: CurrentUser,
    request: Request,
    db: DbSession,
    installation_ids: AccessibleInstallations,
) -> FindingResponse:
    """Triage a finding.

    Human judgement is recorded rather than overwritten: the status change is
    audited with the acting user, and resolved findings keep the note that
    explains *why* they were dismissed.
    """
    if not installation_ids:
        raise FindingNotFound()
    result = await db.execute(_scoped_query(installation_ids).where(Finding.id == finding_id))
    finding = result.scalar_one_or_none()
    if finding is None:
        raise FindingNotFound()

    previous = finding.status
    finding.status = payload.status
    finding.resolution_note = payload.note
    if payload.status in (FindingStatus.RESOLVED.value, FindingStatus.DISMISSED.value):
        finding.resolved_by_id = user.id
        finding.resolved_at = datetime.now(UTC)
    else:
        finding.resolved_by_id = None
        finding.resolved_at = None

    await record_audit(
        db,
        AuditAction.FINDING_STATUS_CHANGED,
        user_id=user.id,
        entity_type="finding",
        entity_id=finding.id,
        context={"from": previous, "to": payload.status},
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(finding)
    return FindingResponse.model_validate(finding)
