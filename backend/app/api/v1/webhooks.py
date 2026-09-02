"""GitHub webhook receiver.

Security posture
----------------
1. The raw body is read *before* parsing and verified with HMAC-SHA256 against
   the configured secret. An unsigned or wrongly signed delivery is rejected
   with 401 and never parsed as JSON.
2. Delivery ids are recorded, so GitHub's at-least-once redelivery cannot cause
   duplicate reviews.
3. The handler returns quickly: all real work is queued.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession
from app.core.config import settings
from app.core.enums import AuditAction
from app.core.errors import ValidationError, WebhookSignatureError
from app.core.logging import get_logger
from app.core.security import verify_webhook_signature
from app.integrations.github.base import PullRequestInfo
from app.integrations.github.factory import get_github_provider
from app.integrations.github.webhook import ParsedWebhook, parse_webhook
from app.models.review import WebhookDelivery
from app.models.user import GitHubInstallation
from app.services.audit import record_audit
from app.services.queue import enqueue_review
from app.services.review_service import (
    ensure_repository_settings,
    find_repository_by_full_name,
    get_or_create_review_run,
    sync_installation_repositories,
    upsert_installation,
    upsert_pull_request,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

MAX_WEBHOOK_BYTES = 5 * 1024 * 1024


async def _record_delivery(
    db: AsyncSession,
    parsed: ParsedWebhook,
    *,
    accepted: bool,
    reason: str | None,
    review_run_id: str | None = None,
) -> None:
    db.add(
        WebhookDelivery(
            delivery_id=parsed.delivery_id,
            event=parsed.event,
            action=parsed.action,
            installation_id=parsed.installation_id,
            repository_full_name=parsed.repository_full_name,
            pr_number=parsed.pr_number,
            head_sha=parsed.head_sha,
            accepted=accepted,
            reason=reason,
            review_run_id=review_run_id,
        )
    )


@router.post("/github", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    response: Response,
    db: DbSession,
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
    x_github_delivery: str | None = Header(default=None, alias="X-GitHub-Delivery"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BYTES:
        raise ValidationError("Webhook body is too large", code="WEBHOOK_TOO_LARGE")

    if not verify_webhook_signature(raw, x_hub_signature_256, settings.GITHUB_WEBHOOK_SECRET):
        logger.warning(
            "webhook.signature_invalid",
            github_event=x_github_event,
            delivery=x_github_delivery,
        )
        raise WebhookSignatureError()

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Webhook body is not valid JSON", code="WEBHOOK_MALFORMED") from exc

    parsed = parse_webhook(x_github_event, x_github_delivery, payload)

    duplicate = await db.scalar(
        select(WebhookDelivery).where(WebhookDelivery.delivery_id == parsed.delivery_id)
    )
    if duplicate is not None:
        response.status_code = status.HTTP_200_OK
        return {
            "status": "duplicate",
            "detail": "This delivery was already processed.",
            "delivery_id": parsed.delivery_id,
        }

    if parsed.event in ("installation", "installation_repositories"):
        result = await _handle_installation_event(db, parsed, payload)
        await db.commit()
        return result

    if not parsed.should_review:
        await _record_delivery(
            db, parsed, accepted=False, reason=parsed.ignore_reason or "not reviewable"
        )
        await record_audit(
            db,
            AuditAction.WEBHOOK_RECEIVED,
            entity_type="webhook",
            entity_id=parsed.delivery_id,
            context={"event": parsed.event, "ignored": parsed.ignore_reason},
        )
        await db.commit()
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "detail": parsed.ignore_reason or "not reviewable"}

    repository = await find_repository_by_full_name(db, parsed.repository_full_name or "")
    if repository is None:
        await _record_delivery(
            db, parsed, accepted=False, reason="repository is not connected"
        )
        await db.commit()
        response.status_code = status.HTTP_200_OK
        return {
            "status": "ignored",
            "detail": (
                f"Repository '{parsed.repository_full_name}' is not connected to this app."
            ),
        }

    config = await ensure_repository_settings(db, repository)
    if not repository.is_active or not config.auto_review_enabled:
        await _record_delivery(db, parsed, accepted=False, reason="auto review disabled")
        await db.commit()
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "detail": "Automatic review is disabled for this repo."}
    if parsed.action == "opened" and not config.review_on_open:
        await _record_delivery(db, parsed, accepted=False, reason="review_on_open disabled")
        await db.commit()
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "detail": "review_on_open is disabled for this repo."}
    if parsed.action == "synchronize" and not config.review_on_synchronize:
        await _record_delivery(
            db, parsed, accepted=False, reason="review_on_synchronize disabled"
        )
        await db.commit()
        response.status_code = status.HTTP_200_OK
        return {"status": "ignored", "detail": "review_on_synchronize is disabled."}

    pr_payload = payload.get("pull_request") or {}
    info = PullRequestInfo.from_api(pr_payload)
    pull_request = await upsert_pull_request(db, repository, info)

    review, created = await get_or_create_review_run(
        db,
        pull_request,
        commit_sha=parsed.head_sha,
        trigger=f"webhook:{parsed.action}",
        force=False,
    )
    await _record_delivery(
        db,
        parsed,
        accepted=True,
        reason=None if created else "review already exists for this commit",
        review_run_id=review.id,
    )
    await record_audit(
        db,
        AuditAction.REVIEW_ENQUEUED,
        entity_type="review_run",
        entity_id=review.id,
        context={"trigger": "webhook", "delivery_id": parsed.delivery_id},
    )
    await db.commit()

    if not created:
        response.status_code = status.HTTP_200_OK
        return {
            "status": "duplicate",
            "review_run_id": review.id,
            "detail": "A review for this commit already exists.",
        }

    dispatch = enqueue_review(review.id)
    logger.info(
        "webhook.review_queued",
        repository=repository.full_name,
        pr=parsed.pr_number,
        head_sha=parsed.head_sha,
        review_run_id=review.id,
        mode=dispatch.mode,
    )
    return {
        "status": "queued",
        "review_run_id": review.id,
        "task_id": dispatch.task_id,
        "detail": dispatch.detail,
    }


async def _handle_installation_event(
    db: AsyncSession, parsed: ParsedWebhook, payload: dict[str, Any]
) -> dict[str, Any]:
    """Keep installation + repository mirroring in sync with GitHub."""
    installation_payload = payload.get("installation") or {}
    installation_id = parsed.installation_id
    if installation_id is None:
        await _record_delivery(db, parsed, accepted=False, reason="no installation id")
        return {"status": "ignored", "detail": "Installation event without an installation id."}

    action = parsed.action or ""
    if action in ("deleted", "suspend"):
        existing = await db.scalar(
            select(GitHubInstallation).where(
                GitHubInstallation.installation_id == installation_id
            )
        )
        if existing is not None:
            existing.is_active = action != "deleted"
            existing.suspended = action == "suspend"
        await _record_delivery(db, parsed, accepted=True, reason=f"installation {action}")
        return {"status": "ok", "detail": f"Installation {action} recorded."}

    provider = get_github_provider()
    try:
        info = await provider.get_installation(installation_id)
    except Exception as exc:
        logger.warning("webhook.installation_lookup_failed", error=str(exc))
        account = (installation_payload.get("account") or {}).get("login") or "unknown"
        from app.integrations.github.base import InstallationInfo

        info = InstallationInfo(installation_id=installation_id, account_login=account)

    installation = await upsert_installation(db, info)
    try:
        total, added = await sync_installation_repositories(db, provider, installation)
    except Exception as exc:  # pragma: no cover - upstream variability
        logger.warning("webhook.repo_sync_failed", error=str(exc))
        total, added = 0, 0

    await _record_delivery(db, parsed, accepted=True, reason=f"installation {action}")
    return {
        "status": "ok",
        "detail": f"Installation synced ({total} repositories, {added} new).",
    }
