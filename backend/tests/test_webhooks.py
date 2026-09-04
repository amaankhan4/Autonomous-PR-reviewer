"""Webhook signature verification, routing and idempotency."""

from __future__ import annotations

import json

from app.core.config import settings
from app.core.security import compute_webhook_signature
from app.demo import fixtures

WEBHOOK_URL = "/api/v1/webhooks/github"


def pr_payload(action: str = "opened", head_sha: str | None = None) -> dict:
    return {
        "action": action,
        "number": 142,
        "installation": {"id": fixtures.DEMO_INSTALLATION_ID},
        "repository": {
            "id": 901234501,
            "full_name": "acme-corp/payments-api",
            "name": "payments-api",
            "owner": {"login": "acme-corp"},
            "default_branch": "main",
            "private": True,
        },
        "pull_request": {
            "id": 900142,
            "number": 142,
            "title": "Add payment retry mechanism",
            "body": "Retries gateway timeouts.",
            "draft": False,
            "state": "open",
            "user": {"login": "dana-eng"},
            "base": {"ref": "main", "sha": fixtures.BASE_SHA},
            "head": {"ref": "feat/payment-retries", "sha": head_sha or fixtures.HEAD_SHA},
            "html_url": "https://github.com/acme-corp/payments-api/pull/142",
            "additions": 40,
            "deletions": 4,
            "changed_files": 2,
        },
    }


def signed(payload: dict, delivery: str, event: str = "pull_request") -> tuple[bytes, dict]:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": compute_webhook_signature(
            body, settings.GITHUB_WEBHOOK_SECRET
        ),
        "Content-Type": "application/json",
    }
    return body, headers


async def test_unsigned_delivery_is_rejected(client):
    response = await client.post(
        WEBHOOK_URL,
        content=json.dumps(pr_payload()),
        headers={"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d1"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_WEBHOOK_SIGNATURE"


async def test_wrong_signature_is_rejected(client):
    body = json.dumps(pr_payload()).encode()
    response = await client.post(
        WEBHOOK_URL,
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "d2",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
        },
    )
    assert response.status_code == 401


async def test_signature_of_a_different_body_is_rejected(client):
    """Signing one payload must not authorise a different one."""
    _, headers = signed(pr_payload(), "d3")
    tampered = json.dumps(pr_payload(head_sha="deadbeef" * 5)).encode()
    response = await client.post(WEBHOOK_URL, content=tampered, headers=headers)
    assert response.status_code == 401


async def test_pull_request_opened_queues_a_review(client, connected, dispatched):
    body, headers = signed(pr_payload("opened"), "delivery-open")
    response = await client.post(WEBHOOK_URL, content=body, headers=headers)
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["status"] == "queued"
    assert ("review", (payload["review_run_id"],)) in dispatched


async def test_duplicate_delivery_is_not_reprocessed(client, connected, dispatched):
    body, headers = signed(pr_payload("opened"), "delivery-dupe")
    first = await client.post(WEBHOOK_URL, content=body, headers=headers)
    second = await client.post(WEBHOOK_URL, content=body, headers=headers)
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert len([call for call in dispatched if call[0] == "review"]) == 1


async def test_same_head_sha_from_a_new_delivery_is_deduped(client, connected, dispatched):
    body1, headers1 = signed(pr_payload("opened"), "delivery-a")
    body2, headers2 = signed(pr_payload("synchronize"), "delivery-b")
    await client.post(WEBHOOK_URL, content=body1, headers=headers1)
    second = await client.post(WEBHOOK_URL, content=body2, headers=headers2)
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert len([call for call in dispatched if call[0] == "review"]) == 1


async def test_new_head_sha_creates_a_new_review(client, connected, dispatched):
    body1, headers1 = signed(pr_payload("opened"), "delivery-c")
    body2, headers2 = signed(
        pr_payload("synchronize", head_sha="a" * 40), "delivery-d"
    )
    first = await client.post(WEBHOOK_URL, content=body1, headers=headers1)
    second = await client.post(WEBHOOK_URL, content=body2, headers=headers2)
    assert second.status_code == 202
    assert first.json()["review_run_id"] != second.json()["review_run_id"]
    assert len([call for call in dispatched if call[0] == "review"]) == 2


async def test_closed_action_is_ignored(client, connected, dispatched):
    body, headers = signed(pr_payload("closed"), "delivery-closed")
    response = await client.post(WEBHOOK_URL, content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert not [call for call in dispatched if call[0] == "review"]


async def test_draft_pull_request_is_ignored(client, connected, dispatched):
    payload = pr_payload("opened")
    payload["pull_request"]["draft"] = True
    body, headers = signed(payload, "delivery-draft")
    response = await client.post(WEBHOOK_URL, content=body, headers=headers)
    assert response.status_code == 200
    assert "draft" in response.json()["detail"].lower()
    assert not dispatched


async def test_unknown_repository_is_ignored(client, connected, dispatched):
    payload = pr_payload("opened")
    payload["repository"]["full_name"] = "someone-else/private-repo"
    body, headers = signed(payload, "delivery-unknown")
    response = await client.post(WEBHOOK_URL, content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert not dispatched


async def test_disabled_auto_review_is_respected(client, token, payments_repo, dispatched):
    from tests.conftest import auth_headers

    await client.patch(
        f"/api/v1/repositories/{payments_repo['id']}/settings",
        json={"auto_review_enabled": False},
        headers=auth_headers(token),
    )
    body, headers = signed(pr_payload("opened"), "delivery-disabled")
    response = await client.post(WEBHOOK_URL, content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert not [call for call in dispatched if call[0] == "review"]


async def test_ping_event_is_acknowledged(client):
    body, headers = signed({"zen": "Design for failure."}, "delivery-ping", event="ping")
    response = await client.post(WEBHOOK_URL, content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


async def test_malformed_pull_request_payload_is_422(client, connected):
    body, headers = signed({"action": "opened"}, "delivery-malformed")
    response = await client.post(WEBHOOK_URL, content=body, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "WEBHOOK_MALFORMED"
