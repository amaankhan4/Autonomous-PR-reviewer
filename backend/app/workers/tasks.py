"""Celery task definitions.

Tasks are thin: they translate a message into a pipeline call and manage retry
policy. All real work lives in :mod:`app.workers.pipeline` so it can be tested
without a broker.
"""

from __future__ import annotations

from typing import Any

from celery import Task

from app.core.logging import get_logger
from app.workers.celery_app import celery_app
from app.workers.pipeline import run_index_sync, run_review_sync

logger = get_logger(__name__)

RETRY_BACKOFF_SECONDS = 30
MAX_RETRIES = 3


@celery_app.task(
    bind=True,
    name="reviews.run",
    max_retries=MAX_RETRIES,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=RETRY_BACKOFF_SECONDS,
    retry_backoff_max=600,
    retry_jitter=True,
)
def review_pull_request(self: Task, review_run_id: str) -> dict[str, Any]:
    """Run the review pipeline for one review run.

    The pipeline records its own failures on the row, so a returned error dict
    is a *handled* failure. Only infrastructure errors bubble up to Celery's
    retry machinery.
    """
    logger.info("task.review.start", review_run_id=review_run_id, attempt=self.request.retries)
    return run_review_sync(review_run_id)


@celery_app.task(
    bind=True,
    name="reviews.index",
    max_retries=2,
    retry_backoff=RETRY_BACKOFF_SECONDS,
    retry_jitter=True,
)
def index_repository(
    self: Task,
    repository_id: str,
    commit_sha: str | None = None,
    full: bool = False,
) -> dict[str, Any]:
    logger.info("task.index.start", repository_id=repository_id, full=full)
    return run_index_sync(repository_id, commit_sha, full)
