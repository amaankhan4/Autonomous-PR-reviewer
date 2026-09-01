"""Job dispatch with a safe fallback.

Production runs Celery + Redis. But the product must also be runnable with a
single command for evaluation, so if the broker is unreachable the job is run
in-process as an asyncio background task instead of being silently dropped.
The response tells the caller which path was taken.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Strong references to inline tasks so the event loop cannot garbage collect them.
_INLINE_TASKS: set[asyncio.Task[Any]] = set()


@dataclass(slots=True)
class Dispatch:
    task_id: str | None
    mode: str  # "celery" | "inline"
    detail: str


def _run_inline(coro_factory: Any, label: str) -> Dispatch:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:  # pragma: no cover - only outside a request context
        asyncio.run(coro_factory())
        return Dispatch(task_id=None, mode="inline", detail=f"{label} ran synchronously")

    task = loop.create_task(coro_factory())
    _INLINE_TASKS.add(task)
    task.add_done_callback(_INLINE_TASKS.discard)
    return Dispatch(
        task_id=None,
        mode="inline",
        detail=f"{label} started in-process (no Celery broker available)",
    )


def enqueue_review(review_run_id: str) -> Dispatch:
    from app.workers.pipeline import execute_review

    if not settings.CELERY_TASK_ALWAYS_EAGER:
        try:
            from app.workers.tasks import review_pull_request

            async_result = review_pull_request.apply_async(
                args=[review_run_id], queue=settings.REVIEW_QUEUE
            )
            return Dispatch(
                task_id=str(async_result.id), mode="celery", detail="Review queued"
            )
        except Exception as exc:
            logger.warning("queue.celery_unavailable", error=str(exc), job="review")

    return _run_inline(lambda: execute_review(review_run_id), "Review")


def enqueue_index(
    repository_id: str, commit_sha: str | None = None, full: bool = False
) -> Dispatch:
    from app.workers.pipeline import execute_index

    if not settings.CELERY_TASK_ALWAYS_EAGER:
        try:
            from app.workers.tasks import index_repository

            async_result = index_repository.apply_async(
                args=[repository_id, commit_sha, full], queue=settings.INDEX_QUEUE
            )
            return Dispatch(
                task_id=str(async_result.id), mode="celery", detail="Indexing queued"
            )
        except Exception as exc:
            logger.warning("queue.celery_unavailable", error=str(exc), job="index")

    return _run_inline(
        lambda: execute_index(repository_id, commit_sha=commit_sha, full=full), "Indexing"
    )


async def broker_reachable() -> bool:
    """Best-effort broker probe used by the health endpoint."""
    if settings.CELERY_TASK_ALWAYS_EAGER:
        return False
    try:
        import redis.asyncio as redis_asyncio

        client = redis_asyncio.from_url(settings.broker_url, socket_connect_timeout=1.5)
        try:
            await client.ping()
            return True
        finally:
            await client.aclose()
    except Exception:
        return False
