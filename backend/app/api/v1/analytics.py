"""Dashboard analytics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import Float, cast, func, select

from app.api.deps import AccessibleInstallations, DbSession
from app.core.config import settings
from app.core.enums import FindingStatus, PullRequestState, ReviewStatus
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.models.review import Finding, LLMUsage, ReviewRun
from app.schemas.analytics import (
    AnalyticsResponse,
    CountPoint,
    OverviewStats,
    QueueStats,
    TrendPoint,
)
from app.services.queue import broker_reachable

router = APIRouter(prefix="/analytics", tags=["analytics"])

IN_PROGRESS = (
    ReviewStatus.QUEUED.value,
    ReviewStatus.PROCESSING.value,
    ReviewStatus.ANALYZING.value,
    ReviewStatus.REVIEWING.value,
    ReviewStatus.PUBLISHING.value,
)


def _empty(window_days: int) -> AnalyticsResponse:
    return AnalyticsResponse(
        overview=OverviewStats(demo_mode=settings.demo_mode),
        generated_at=datetime.now(UTC),
        window_days=window_days,
    )


@router.get("", response_model=AnalyticsResponse)
async def get_analytics(
    db: DbSession,
    installation_ids: AccessibleInstallations,
    window_days: int = Query(default=30, ge=1, le=365),
    repository_id: str | None = Query(default=None),
) -> AnalyticsResponse:
    if not installation_ids:
        return _empty(window_days)

    since = datetime.now(UTC) - timedelta(days=window_days)
    repo_filter = [Repository.id == repository_id] if repository_id else []

    repositories = int(
        await db.scalar(
            select(func.count(Repository.id)).where(
                Repository.installation_id.in_(installation_ids), *repo_filter
            )
        )
        or 0
    )
    active_repositories = int(
        await db.scalar(
            select(func.count(Repository.id)).where(
                Repository.installation_id.in_(installation_ids),
                Repository.is_active.is_(True),
                *repo_filter,
            )
        )
        or 0
    )
    pr_base = (
        select(func.count(PullRequest.id))
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(Repository.installation_id.in_(installation_ids), *repo_filter)
    )
    pull_requests = int(await db.scalar(pr_base) or 0)
    open_pull_requests = int(
        await db.scalar(pr_base.where(PullRequest.state == PullRequestState.OPEN.value)) or 0
    )

    review_count_base = (
        select(func.count(ReviewRun.id))
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(Repository.installation_id.in_(installation_ids), *repo_filter)
    )
    reviews_total = int(await db.scalar(review_count_base) or 0)
    reviews_completed = int(
        await db.scalar(review_count_base.where(ReviewRun.status == ReviewStatus.COMPLETED.value))
        or 0
    )
    reviews_failed = int(
        await db.scalar(review_count_base.where(ReviewRun.status == ReviewStatus.FAILED.value))
        or 0
    )
    reviews_in_progress = int(
        await db.scalar(review_count_base.where(ReviewRun.status.in_(IN_PROGRESS))) or 0
    )
    published_reviews = int(
        await db.scalar(review_count_base.where(ReviewRun.published.is_(True))) or 0
    )

    avg_risk = await db.scalar(
        select(func.avg(cast(ReviewRun.risk_score, Float)))
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(
            Repository.installation_id.in_(installation_ids),
            ReviewRun.status == ReviewStatus.COMPLETED.value,
            *repo_filter,
        )
    )
    avg_duration = await db.scalar(
        select(func.avg(cast(ReviewRun.duration_ms, Float)))
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(
            Repository.installation_id.in_(installation_ids),
            ReviewRun.duration_ms.is_not(None),
            *repo_filter,
        )
    )

    finding_count_base = (
        select(func.count(Finding.id))
        .join(ReviewRun, ReviewRun.id == Finding.review_run_id)
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(Repository.installation_id.in_(installation_ids), *repo_filter)
    )
    findings_total = int(await db.scalar(finding_count_base) or 0)
    findings_open = int(
        await db.scalar(finding_count_base.where(Finding.status == FindingStatus.OPEN.value)) or 0
    )
    findings_resolved = int(
        await db.scalar(
            finding_count_base.where(
                Finding.status.in_(
                    (FindingStatus.RESOLVED.value, FindingStatus.DISMISSED.value)
                )
            )
        )
        or 0
    )
    critical_open = int(
        await db.scalar(
            finding_count_base.where(
                Finding.severity == "CRITICAL", Finding.status == FindingStatus.OPEN.value
            )
        )
        or 0
    )
    high_open = int(
        await db.scalar(
            finding_count_base.where(
                Finding.severity == "HIGH", Finding.status == FindingStatus.OPEN.value
            )
        )
        or 0
    )

    usage_row = await db.execute(
        select(
            func.coalesce(func.sum(LLMUsage.estimated_cost_usd), 0.0),
            func.coalesce(func.sum(LLMUsage.input_tokens + LLMUsage.output_tokens), 0),
        )
        .join(ReviewRun, ReviewRun.id == LLMUsage.review_run_id)
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(Repository.installation_id.in_(installation_ids), *repo_filter)
    )
    llm_cost, llm_tokens = usage_row.one()

    overview = OverviewStats(
        repositories=repositories,
        active_repositories=active_repositories,
        pull_requests=pull_requests,
        open_pull_requests=open_pull_requests,
        reviews_total=reviews_total,
        reviews_completed=reviews_completed,
        reviews_failed=reviews_failed,
        reviews_in_progress=reviews_in_progress,
        findings_total=findings_total,
        findings_open=findings_open,
        findings_resolved=findings_resolved,
        critical_open=critical_open,
        high_open=high_open,
        avg_risk_score=round(float(avg_risk or 0.0), 2),
        avg_review_duration_ms=int(avg_duration or 0),
        published_reviews=published_reviews,
        llm_cost_usd=round(float(llm_cost or 0.0), 4),
        llm_tokens=int(llm_tokens or 0),
        demo_mode=settings.demo_mode,
    )

    async def breakdown(column) -> list[CountPoint]:
        rows = await db.execute(
            select(column, func.count(Finding.id))
            .join(ReviewRun, ReviewRun.id == Finding.review_run_id)
            .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
            .join(Repository, Repository.id == PullRequest.repository_id)
            .where(Repository.installation_id.in_(installation_ids), *repo_filter)
            .group_by(column)
            .order_by(func.count(Finding.id).desc())
        )
        return [CountPoint(label=str(label), count=int(count)) for label, count in rows.all()]

    severity_breakdown = await breakdown(Finding.severity)
    category_breakdown = await breakdown(Finding.category)
    status_breakdown = await breakdown(Finding.status)
    source_breakdown = await breakdown(Finding.source)

    top_repo_rows = await db.execute(
        select(Repository.full_name, func.count(Finding.id))
        .join(PullRequest, PullRequest.repository_id == Repository.id)
        .join(ReviewRun, ReviewRun.pull_request_id == PullRequest.id)
        .join(Finding, Finding.review_run_id == ReviewRun.id)
        .where(Repository.installation_id.in_(installation_ids), *repo_filter)
        .group_by(Repository.full_name)
        .order_by(func.count(Finding.id).desc())
        .limit(10)
    )
    top_repositories = [
        CountPoint(label=name, count=int(count)) for name, count in top_repo_rows.all()
    ]

    top_file_rows = await db.execute(
        select(Finding.file_path, func.count(Finding.id))
        .join(ReviewRun, ReviewRun.id == Finding.review_run_id)
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(Repository.installation_id.in_(installation_ids), *repo_filter)
        .group_by(Finding.file_path)
        .order_by(func.count(Finding.id).desc())
        .limit(10)
    )
    top_files = [CountPoint(label=path, count=int(count)) for path, count in top_file_rows.all()]

    trend_rows = await db.execute(
        select(
            func.date(ReviewRun.created_at).label("day"),
            func.count(ReviewRun.id),
            func.coalesce(func.sum(ReviewRun.findings_count), 0),
            func.coalesce(func.avg(cast(ReviewRun.risk_score, Float)), 0.0),
        )
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(
            Repository.installation_id.in_(installation_ids),
            ReviewRun.created_at >= since,
            *repo_filter,
        )
        .group_by(func.date(ReviewRun.created_at))
        .order_by(func.date(ReviewRun.created_at))
    )
    trend = [
        TrendPoint(
            date=str(day),
            reviews=int(reviews or 0),
            findings=int(findings or 0),
            avg_risk=round(float(risk or 0.0), 2),
        )
        for day, reviews, findings, risk in trend_rows.all()
    ]

    return AnalyticsResponse(
        overview=overview,
        severity_breakdown=severity_breakdown,
        category_breakdown=category_breakdown,
        status_breakdown=status_breakdown,
        source_breakdown=source_breakdown,
        top_repositories=top_repositories,
        top_files=top_files,
        trend=trend,
        generated_at=datetime.now(UTC),
        window_days=window_days,
    )


@router.get("/queue", response_model=QueueStats)
async def get_queue_stats(
    db: DbSession, installation_ids: AccessibleInstallations
) -> QueueStats:
    if not installation_ids:
        return QueueStats(
            eager_mode=settings.CELERY_TASK_ALWAYS_EAGER,
            broker_reachable=await broker_reachable(),
        )

    since = datetime.now(UTC) - timedelta(hours=24)
    base = (
        select(func.count(ReviewRun.id))
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .join(Repository, Repository.id == PullRequest.repository_id)
        .where(Repository.installation_id.in_(installation_ids))
    )
    return QueueStats(
        queued=int(await db.scalar(base.where(ReviewRun.status == ReviewStatus.QUEUED.value)) or 0),
        processing=int(
            await db.scalar(
                base.where(
                    ReviewRun.status.in_(
                        (
                            ReviewStatus.PROCESSING.value,
                            ReviewStatus.ANALYZING.value,
                            ReviewStatus.REVIEWING.value,
                            ReviewStatus.PUBLISHING.value,
                        )
                    )
                )
            )
            or 0
        ),
        failed_last_24h=int(
            await db.scalar(
                base.where(
                    ReviewRun.status == ReviewStatus.FAILED.value,
                    ReviewRun.created_at >= since,
                )
            )
            or 0
        ),
        completed_last_24h=int(
            await db.scalar(
                base.where(
                    ReviewRun.status == ReviewStatus.COMPLETED.value,
                    ReviewRun.created_at >= since,
                )
            )
            or 0
        ),
        eager_mode=settings.CELERY_TASK_ALWAYS_EAGER,
        broker_reachable=await broker_reachable(),
    )
