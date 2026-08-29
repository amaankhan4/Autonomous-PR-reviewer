"""GitHub webhook payload parsing and validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.core.errors import ValidationError

#: Only these pull-request actions trigger a review.
REVIEWABLE_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}

SUPPORTED_EVENTS = {
    "ping",
    "pull_request",
    "installation",
    "installation_repositories",
    "push",
}

WebhookOutcome = Literal["accepted", "ignored", "rejected"]


@dataclass(slots=True)
class ParsedWebhook:
    event: str
    action: str | None
    delivery_id: str
    installation_id: int | None = None
    repository_full_name: str | None = None
    repository_github_id: int | None = None
    pr_number: int | None = None
    head_sha: str | None = None
    base_sha: str | None = None
    title: str | None = None
    author: str | None = None
    draft: bool = False
    should_review: bool = False
    ignore_reason: str | None = None
    raw: dict[str, Any] | None = None

    @property
    def idempotency_key(self) -> str | None:
        if not (self.repository_full_name and self.pr_number and self.head_sha):
            return None
        return f"{self.repository_full_name}#{self.pr_number}@{self.head_sha}"


def parse_webhook(
    event: str | None, delivery_id: str | None, payload: dict[str, Any]
) -> ParsedWebhook:
    """Convert a raw webhook body into a validated, typed structure.

    Malformed payloads raise :class:`ValidationError` (HTTP 422) rather than
    being silently ignored, so delivery problems are visible in GitHub's UI.
    """
    if not event:
        raise ValidationError("Missing X-GitHub-Event header", code="WEBHOOK_MISSING_EVENT")
    if not isinstance(payload, dict):
        raise ValidationError("Webhook body must be a JSON object", code="WEBHOOK_MALFORMED")

    action = payload.get("action")
    if action is not None and not isinstance(action, str):
        raise ValidationError("Webhook 'action' must be a string", code="WEBHOOK_MALFORMED")

    installation = payload.get("installation") or {}
    repository = payload.get("repository") or {}

    parsed = ParsedWebhook(
        event=event,
        action=action,
        delivery_id=delivery_id or "unknown",
        installation_id=_as_int(installation.get("id")),
        repository_full_name=repository.get("full_name"),
        repository_github_id=_as_int(repository.get("id")),
        raw=payload,
    )

    if event == "ping":
        parsed.ignore_reason = "ping"
        return parsed

    if event != "pull_request":
        parsed.ignore_reason = f"event '{event}' does not trigger a review"
        return parsed

    pr = payload.get("pull_request")
    if not isinstance(pr, dict):
        raise ValidationError(
            "pull_request event is missing the 'pull_request' object",
            code="WEBHOOK_MALFORMED",
        )

    number = _as_int(pr.get("number") or payload.get("number"))
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    head_sha = head.get("sha")

    if number is None:
        raise ValidationError("pull_request event has no PR number", code="WEBHOOK_MALFORMED")
    if not head_sha or not isinstance(head_sha, str):
        raise ValidationError("pull_request event has no head SHA", code="WEBHOOK_MALFORMED")
    if not parsed.repository_full_name:
        raise ValidationError("pull_request event has no repository", code="WEBHOOK_MALFORMED")
    if parsed.installation_id is None:
        raise ValidationError(
            "pull_request event has no installation id; the App must be installed",
            code="WEBHOOK_MALFORMED",
        )

    parsed.pr_number = number
    parsed.head_sha = head_sha
    parsed.base_sha = base.get("sha")
    parsed.title = pr.get("title")
    parsed.author = (pr.get("user") or {}).get("login")
    parsed.draft = bool(pr.get("draft"))

    if action not in REVIEWABLE_ACTIONS:
        parsed.ignore_reason = f"action '{action}' does not trigger a review"
        return parsed
    if parsed.draft and action != "ready_for_review":
        parsed.ignore_reason = "pull request is a draft"
        return parsed

    parsed.should_review = True
    return parsed


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
