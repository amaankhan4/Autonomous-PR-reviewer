"""Celery application.

Two queues with different characteristics:

* ``review_queue`` -- latency sensitive, one PR at a time per worker slot.
* ``index_queue``  -- throughput oriented, tolerant of being slow.

Separating them means a large repository being indexed can never starve a
developer waiting for a review on their pull request.
"""

from __future__ import annotations

import ssl

from celery import Celery

from app.core.config import settings
from app.core.logging import configure_logging

configure_logging()

celery_app = Celery(
    "pr_reviewer",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
    broker_connection_retry_on_startup=True,
    result_expires=60 * 60 * 24,
    task_time_limit=settings.REVIEW_JOB_TIMEOUT_SECONDS + 120,
    task_soft_time_limit=settings.REVIEW_JOB_TIMEOUT_SECONDS,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_eager_propagates=False,
    task_default_queue=settings.REVIEW_QUEUE,
    task_routes={
        "reviews.run": {"queue": settings.REVIEW_QUEUE},
        "reviews.index": {"queue": settings.INDEX_QUEUE},
    },
    # A hosted Redis plan often exposes a single shared database. Prefixing every
    # key keeps this application's queues and results from colliding with any
    # other tenant of the same database.
    broker_transport_options={"global_keyprefix": settings.REDIS_KEY_PREFIX},
    result_backend_transport_options={"global_keyprefix": settings.REDIS_KEY_PREFIX},
)

# Celery refuses to connect over ``rediss://`` unless the TLS policy is stated
# explicitly. Upstash presents a certificate from a public CA, so requiring
# verification is both correct and safe.
if settings.broker_url.startswith("rediss://"):
    celery_app.conf.broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_REQUIRED}
if settings.result_backend.startswith("rediss://"):
    celery_app.conf.redis_backend_use_ssl = {"ssl_cert_reqs": ssl.CERT_REQUIRED}
