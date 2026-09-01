"""The review pipeline executed by the worker.

Owns everything the engine deliberately does not: fetching from GitHub,
persistence, progress reporting, publication and failure handling.

    queued -> processing -> analyzing -> reviewing -> publishing -> completed
                                                              \\-> failed

Every stage writes its progress back to the ``review_runs`` row, which is what
the dashboard polls. A failure is always recorded on the row (never swallowed),
because a review that silently disappears is worse than one that visibly failed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.enums import AuditAction, IndexStatus, ReviewStatus
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.integrations.github.base import ChangedFile, GitHubProvider
from app.integrations.github.factory import get_github_provider
from app.integrations.llm.factory import get_llm_provider
from app.integrations.vector.embeddings import get_embedding_provider
from app.integrations.vector.factory import get_vector_store
from app.models.pull_request import PullRequest
from app.models.repository import Repository, RepositoryIndex, RepositorySettings
from app.models.review import Finding, LLMUsage, ReviewComment, ReviewRun
from app.models.user import GitHubInstallation
from app.services.audit import record_audit
from app.services.context_builder import ReviewContextBuilder
from app.services.github_publisher import GitHubPublisher, build_plan, escalate_event
from app.services.indexing_service import IndexingService
from app.services.review_engine import ReviewEngine, ReviewResult, ReviewSettings
from app.services.review_service import ensure_repository_settings, historical_findings

logger = get_logger(__name__)

MAX_CONTEXT_FILES = 40


# --------------------------------------------------------------------- helpers
def _settings_payload(config: RepositorySettings | None) -> dict[str, Any]:
    if config is None:
        return {}
    return {
        "min_severity": config.min_severity,
        "max_findings": settings.MAX_FINDINGS_PUBLISHED,
        "min_confidence": config.min_confidence,
        "enabled_analyzers": list(config.enabled_analyzers or []),
        "include_deterministic": True,
    }


def _excluded(path: str, patterns: list[str]) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(path, pattern) for pattern in patterns)


async def _load_run(db: AsyncSession, review_run_id: str) -> ReviewRun | None:
    result = await db.execute(
        select(ReviewRun)
        .options(
            selectinload(ReviewRun.pull_request)
            .selectinload(PullRequest.repository)
            .selectinload(Repository.installation),
            selectinload(ReviewRun.pull_request)
            .selectinload(PullRequest.repository)
            .selectinload(Repository.settings),
        )
        .where(ReviewRun.id == review_run_id)
    )
    return result.scalar_one_or_none()


async def _fetch_file_contents(
    provider: GitHubProvider,
    installation_id: int,
    full_name: str,
    ref: str,
    changed: list[ChangedFile],
) -> dict[str, str]:
    """Read the head version of each changed file, tolerating individual failures."""
    contents: dict[str, str] = {}
    for entry in changed[:MAX_CONTEXT_FILES]:
        if entry.status == "removed":
            continue
        try:
            text = await provider.get_file(installation_id, full_name, entry.filename, ref)
        except Exception as exc:  # pragma: no cover - network variability
            logger.warning(
                "pipeline.file_fetch_failed", file=entry.filename, error=str(exc)
            )
            continue
        if text is None:
            continue
        if len(text.encode("utf-8", errors="ignore")) > settings.MAX_FILE_BYTES:
            text = text[: settings.MAX_FILE_BYTES]
        contents[entry.filename] = text
    return contents


# ------------------------------------------------------------------- indexing
async def execute_index(
    repository_id: str,
    *,
    commit_sha: str | None = None,
    full: bool = False,
) -> dict[str, Any]:
    """Index (or incrementally re-index) a repository into the vector store."""
    async with session_scope() as db:
        repository = await db.get(Repository, repository_id)
        if repository is None:
            return {"error": "repository_not_found", "repository_id": repository_id}
        installation = await db.get(GitHubInstallation, repository.installation_id)
        if installation is None:
            return {"error": "installation_not_found", "repository_id": repository_id}

        ref = commit_sha or repository.default_branch
        installation_gh_id = installation.installation_id
        full_name = repository.full_name
        record = RepositoryIndex(
            repository_id=repository.id,
            commit_sha=ref,
            status=IndexStatus.INDEXING.value,
            incremental=not full,
            started_at=datetime.now(UTC),
        )
        db.add(record)
        await db.flush()
        record_id = record.id

    provider = get_github_provider()
    store = get_vector_store()
    embedder = get_embedding_provider()

    try:
        async with session_scope() as db:
            repository = await db.get(Repository, repository_id)
            if repository is None:
                return {"error": "repository_not_found", "repository_id": repository_id}
            config = await ensure_repository_settings(db, repository)
            excluded = list(config.excluded_paths or [])

            listing = await provider.list_files(installation_gh_id, full_name, ref)
            paths = [
                str(item["path"])
                for item in listing
                if item.get("path") and not _excluded(str(item["path"]), excluded)
            ]

            async def read_file(path: str) -> str | None:
                return await provider.get_file(installation_gh_id, full_name, path, ref)

            indexer = IndexingService(store, embedder, session=db)
            if full:
                await indexer.clear_repository(repository.id)
            report = await indexer.index_repository(
                repository_id=repository.id,
                commit_sha=ref,
                files=paths,
                read_file=read_file,
                excluded_paths=excluded,
                incremental=not full,
            )

            record = await db.get(RepositoryIndex, record_id)
            if record is not None:
                record.status = IndexStatus.COMPLETED.value
                record.files_indexed = report.files_indexed
                record.files_skipped = report.files_skipped
                record.chunks_indexed = report.chunks_indexed
                record.chunks_deleted = report.chunks_deleted
                record.duration_ms = report.duration_ms
                record.completed_at = datetime.now(UTC)
            return {"repository_id": repository_id, "index_id": record_id, **report.to_dict()}
    except Exception as exc:
        logger.exception("index.failed", repository_id=repository_id, error=str(exc))
        async with session_scope() as db:
            record = await db.get(RepositoryIndex, record_id)
            if record is not None:
                record.status = IndexStatus.FAILED.value
                record.error_message = f"{type(exc).__name__}: {exc}"[:2000]
                record.completed_at = datetime.now(UTC)
        return {"repository_id": repository_id, "index_id": record_id, "error": str(exc)}


async def _ensure_indexed(
    db: AsyncSession,
    repository: Repository,
    provider: GitHubProvider,
    installation_gh_id: int,
    base_sha: str,
    excluded: list[str],
) -> dict[str, Any]:
    """Make sure retrieval has something to retrieve.

    Indexing inline on first review keeps the demo path single-command, and an
    index failure must degrade retrieval, never fail the review.
    """
    existing = await db.execute(
        select(RepositoryIndex)
        .where(
            RepositoryIndex.repository_id == repository.id,
            RepositoryIndex.status == IndexStatus.COMPLETED.value,
        )
        .limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return {"skipped": "already_indexed"}

    try:
        listing = await provider.list_files(installation_gh_id, repository.full_name, base_sha)
        paths = [
            str(item["path"])
            for item in listing
            if item.get("path") and not _excluded(str(item["path"]), excluded)
        ]

        async def read_file(path: str) -> str | None:
            return await provider.get_file(
                installation_gh_id, repository.full_name, path, base_sha
            )

        indexer = IndexingService(get_vector_store(), get_embedding_provider(), session=db)
        report = await indexer.index_repository(
            repository_id=repository.id,
            commit_sha=base_sha,
            files=paths,
            read_file=read_file,
            excluded_paths=excluded,
            incremental=True,
        )
        db.add(
            RepositoryIndex(
                repository_id=repository.id,
                commit_sha=base_sha,
                status=IndexStatus.COMPLETED.value,
                files_indexed=report.files_indexed,
                files_skipped=report.files_skipped,
                chunks_indexed=report.chunks_indexed,
                chunks_deleted=report.chunks_deleted,
                incremental=True,
                duration_ms=report.duration_ms,
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )
        await db.flush()
        return report.to_dict()
    except Exception as exc:
        logger.warning("pipeline.index_failed", repository=repository.full_name, error=str(exc))
        return {"error": str(exc)}


# --------------------------------------------------------------------- review
async def execute_review(review_run_id: str) -> dict[str, Any]:
    """Run one review end to end. Never raises: failures land on the row."""
    started = datetime.now(UTC)

    async with session_scope() as db:
        review = await _load_run(db, review_run_id)
        if review is None:
            logger.error("pipeline.run_missing", review_run_id=review_run_id)
            return {"error": "review_run_not_found", "review_run_id": review_run_id}
        if review.status in (ReviewStatus.COMPLETED.value, ReviewStatus.CANCELLED.value):
            return {"review_run_id": review_run_id, "status": review.status, "skipped": True}

        review.status = ReviewStatus.PROCESSING.value
        review.stage = "starting"
        review.progress = 5
        review.attempts += 1
        review.started_at = started
        review.error_message = None

        pull_request = review.pull_request
        repository = pull_request.repository
        installation = repository.installation
        config = repository.settings or await ensure_repository_settings(db, repository)

        snapshot = {
            "installation_gh_id": installation.installation_id,
            "full_name": repository.full_name,
            "repository_id": repository.id,
            "pr_number": pull_request.github_pr_number,
            "commit_sha": review.commit_sha,
            "base_sha": pull_request.base_sha,
            "publish": bool(config.publish_to_github and settings.PUBLISH_REVIEWS),
            "review_event": config.review_event,
            "excluded_paths": list(config.excluded_paths or []),
            "max_files": config.max_files_per_review,
            "engine_settings": _settings_payload(config),
            "pull_request": {
                "repository": repository.full_name,
                "number": pull_request.github_pr_number,
                "title": pull_request.title,
                "author": pull_request.author,
                "body": pull_request.body,
                "base_branch": pull_request.base_branch,
                "head_branch": pull_request.head_branch,
                "base_sha": pull_request.base_sha,
                "head_sha": review.commit_sha,
            },
        }

    provider = get_github_provider()

    async def report_progress(stage: str, percent: int) -> None:
        status_by_stage = {
            "parsing_diff": ReviewStatus.ANALYZING,
            "running_analyzers": ReviewStatus.ANALYZING,
            "building_context": ReviewStatus.ANALYZING,
            "reasoning": ReviewStatus.REVIEWING,
            "validating": ReviewStatus.REVIEWING,
            "scoring": ReviewStatus.REVIEWING,
        }
        async with session_scope() as db:
            run = await db.get(ReviewRun, review_run_id)
            if run is None or run.status in (
                ReviewStatus.CANCELLED.value,
                ReviewStatus.FAILED.value,
            ):
                return
            run.stage = stage
            run.progress = max(run.progress, percent)
            run.status = status_by_stage.get(stage, ReviewStatus(run.status)).value

    try:
        changed = await provider.get_changed_files(
            snapshot["installation_gh_id"], snapshot["full_name"], snapshot["pr_number"]
        )
        changed = [
            entry
            for entry in changed
            if not _excluded(entry.filename, snapshot["excluded_paths"])
        ][: snapshot["max_files"]]
        if not changed:
            return await _finish_empty(review_run_id, "No reviewable files in this pull request.")

        file_contents = await _fetch_file_contents(
            provider,
            snapshot["installation_gh_id"],
            snapshot["full_name"],
            snapshot["commit_sha"],
            changed,
        )

        async with session_scope() as db:
            repository = await db.get(Repository, snapshot["repository_id"])
            assert repository is not None
            index_report = await _ensure_indexed(
                db,
                repository,
                provider,
                snapshot["installation_gh_id"],
                snapshot["base_sha"] or snapshot["commit_sha"],
                snapshot["excluded_paths"],
            )
            history = await historical_findings(
                db, snapshot["repository_id"], exclude_run_id=review_run_id
            )

        engine = ReviewEngine(
            llm=get_llm_provider(),
            context_builder=ReviewContextBuilder(
                vector_store=get_vector_store(), embedder=get_embedding_provider()
            ),
            review_settings=ReviewSettings.from_mapping(snapshot["engine_settings"]),
            progress=report_progress,
        )
        result = await engine.run(
            repository_full_name=snapshot["full_name"],
            repository_id=snapshot["repository_id"],
            pull_request=snapshot["pull_request"],
            changed_files=[entry.to_dict() for entry in changed],
            file_contents=file_contents,
            historical_findings=history,
        )

        published = None
        if snapshot["publish"]:
            await report_progress("publishing", 92)
            async with session_scope() as db:
                run = await db.get(ReviewRun, review_run_id)
                if run is not None:
                    run.status = ReviewStatus.PUBLISHING.value
            published = await _publish(provider, snapshot, result)

        return await _persist_success(review_run_id, snapshot, result, published, index_report)

    except Exception as exc:
        logger.exception("pipeline.failed", review_run_id=review_run_id, error=str(exc))
        message = exc.message if isinstance(exc, AppError) else f"{type(exc).__name__}: {exc}"
        async with session_scope() as db:
            run = await db.get(ReviewRun, review_run_id)
            if run is not None:
                run.status = ReviewStatus.FAILED.value
                run.stage = "failed"
                run.error_message = message[:2000]
                run.completed_at = datetime.now(UTC)
                run.duration_ms = int(
                    (datetime.now(UTC) - started).total_seconds() * 1000
                )
                await record_audit(
                    db,
                    AuditAction.REVIEW_FAILED,
                    entity_type="review_run",
                    entity_id=review_run_id,
                    context={"error": message[:500]},
                )
        return {"review_run_id": review_run_id, "status": "failed", "error": message}


async def _publish(
    provider: GitHubProvider, snapshot: dict[str, Any], result: ReviewResult
) -> dict[str, Any] | None:
    positions: dict[str, dict[int, int]] = {}
    for file_diff in result.diff.files:
        mapping: dict[int, int] = {}
        for line in file_diff.added_line_numbers | file_diff.context_line_numbers:
            position = file_diff.position_for_line(line)
            if position is not None:
                mapping[line] = position
        positions[file_diff.path] = mapping

    plan = build_plan(
        summary=result.summary,
        findings=result.findings,
        risk=result.risk,
        diff_positions=positions,
        suppressed_count=len(result.validation.suppressed),
        context_stats=result.context.stats,
        analyzers=result.bundle.summary(),
        insufficient_evidence=result.llm_output.insufficient_evidence,
        llm_failed=result.llm_failed,
        warnings=result.warnings,
        event=escalate_event(result.risk, result.findings),
    )
    try:
        review = await GitHubPublisher(provider).publish(
            installation_id=snapshot["installation_gh_id"],
            repository_full_name=snapshot["full_name"],
            pr_number=snapshot["pr_number"],
            commit_sha=snapshot["commit_sha"],
            plan=plan,
        )
    except Exception as exc:
        logger.error("pipeline.publish_failed", error=str(exc))
        return {"error": f"{type(exc).__name__}: {exc}", "plan": plan}
    return {"review": review, "plan": plan}


async def _persist_success(
    review_run_id: str,
    snapshot: dict[str, Any],
    result: ReviewResult,
    published: dict[str, Any] | None,
    index_report: dict[str, Any],
) -> dict[str, Any]:
    async with session_scope() as db:
        run = await db.get(ReviewRun, review_run_id)
        if run is None:
            return {"error": "review_run_not_found", "review_run_id": review_run_id}

        await db.execute(Finding.__table__.delete().where(Finding.review_run_id == run.id))
        await db.execute(
            ReviewComment.__table__.delete().where(ReviewComment.review_run_id == run.id)
        )

        finding_ids: dict[str, str] = {}
        for item in result.findings:
            finding = Finding(
                review_run_id=run.id,
                fingerprint=item.fingerprint,
                file_path=item.file_path,
                line_number=item.line_number,
                start_line=item.start_line,
                end_line=item.end_line,
                severity=str(item.severity),
                category=str(item.category),
                title=item.title[:400],
                description=item.description,
                evidence=item.evidence,
                code_snippet=item.code_snippet,
                suggested_fix=item.suggested_fix,
                confidence=item.confidence,
                confidence_label=str(item.confidence_label),
                source=item.source,
                rule_id=item.rule_id,
                related_symbols=item.related_symbols or None,
                historical_context=item.historical_context,
                published=bool(published and not published.get("error")),
            )
            db.add(finding)
            await db.flush()
            finding_ids[item.fingerprint] = finding.id

        analysis = result.analysis_payload()
        analysis["index"] = index_report

        run.status = ReviewStatus.COMPLETED.value
        run.stage = "completed"
        run.progress = 100
        run.risk_score = result.risk.score
        run.risk_band = str(result.risk.band)
        run.risk_factors = [factor.to_dict() for factor in result.risk.factors]
        run.files_analyzed = len(result.diff.files)
        run.files_skipped = len(result.diff.dropped_files)
        run.findings_count = len(result.findings)
        run.suppressed_count = len(result.validation.suppressed)
        run.summary = result.summary
        run.analysis = analysis
        run.static_findings = [
            {
                "tool": raw.tool,
                "rule_id": raw.rule_id,
                "file_path": raw.file_path,
                "line_number": raw.line_number,
                "severity": str(raw.severity),
                "title": raw.title,
                "message": raw.message,
            }
            for raw in result.bundle.findings[:200]
        ]
        run.context_stats = result.context.stats
        run.completed_at = datetime.now(UTC)
        if run.started_at is not None:
            started_at = run.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=UTC)
            run.duration_ms = int((run.completed_at - started_at).total_seconds() * 1000)

        for usage in result.usages:
            db.add(
                LLMUsage(
                    review_run_id=run.id,
                    provider=usage.provider,
                    model=usage.model,
                    operation=usage.operation,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    latency_ms=usage.latency_ms,
                    estimated_cost_usd=usage.estimated_cost_usd,
                    success=usage.success,
                    error_message=usage.error_message,
                )
            )

        if published and not published.get("error"):
            review = published["review"]
            plan = published["plan"]
            run.published = True
            run.github_review_id = review.review_id
            run.github_review_url = review.html_url
            db.add(
                ReviewComment(
                    review_run_id=run.id,
                    kind="summary",
                    body=plan.summary_body,
                    github_comment_id=review.review_id,
                    github_url=review.html_url,
                    delivered=True,
                )
            )
            rejected = {
                (item.get("path"), item.get("line")) for item in review.rejected_comments
            }
            for comment in plan.inline_comments:
                was_rejected = (comment.path, comment.line) in rejected
                db.add(
                    ReviewComment(
                        review_run_id=run.id,
                        kind="inline",
                        file_path=comment.path,
                        line_number=comment.line,
                        body=comment.body,
                        github_url=review.html_url,
                        delivered=not was_rejected,
                        error_message=(
                            "GitHub rejected this position (line not in diff)"
                            if was_rejected
                            else None
                        ),
                    )
                )
            await record_audit(
                db,
                AuditAction.REVIEW_PUBLISHED,
                entity_type="review_run",
                entity_id=run.id,
                context={"github_review_id": review.review_id},
            )
        elif published and published.get("error"):
            run.error_message = f"Published failed: {published['error']}"[:2000]

        await record_audit(
            db,
            AuditAction.REVIEW_COMPLETED,
            entity_type="review_run",
            entity_id=run.id,
            context={
                "findings": run.findings_count,
                "risk_score": run.risk_score,
                "published": run.published,
            },
        )

        logger.info(
            "pipeline.completed",
            review_run_id=run.id,
            findings=run.findings_count,
            risk=run.risk_score,
            published=run.published,
        )
        return {
            "review_run_id": run.id,
            "status": run.status,
            "findings": run.findings_count,
            "risk_score": run.risk_score,
            "published": run.published,
        }


async def _finish_empty(review_run_id: str, message: str) -> dict[str, Any]:
    async with session_scope() as db:
        run = await db.get(ReviewRun, review_run_id)
        if run is None:
            return {"error": "review_run_not_found"}
        run.status = ReviewStatus.COMPLETED.value
        run.stage = "completed"
        run.progress = 100
        run.summary = message
        run.findings_count = 0
        run.completed_at = datetime.now(UTC)
    return {"review_run_id": review_run_id, "status": "completed", "findings": 0}


def run_review_sync(review_run_id: str) -> dict[str, Any]:
    """Synchronous entry point for Celery workers."""
    return asyncio.run(execute_review(review_run_id))


def run_index_sync(
    repository_id: str, commit_sha: str | None = None, full: bool = False
) -> dict[str, Any]:
    return asyncio.run(execute_index(repository_id, commit_sha=commit_sha, full=full))
