"""Finding triage and analytics aggregation."""

from __future__ import annotations

import pytest

from app.workers.pipeline import execute_review
from tests.conftest import auth_headers


@pytest.fixture
async def reviewed(client, token, payments_repo, dispatched) -> dict:
    imported = await client.post(
        "/api/v1/pull-requests/import",
        json={"repository_id": payments_repo["id"], "number": 142},
        headers=auth_headers(token),
    )
    review_id = imported.json()["review_run_id"]
    await execute_review(review_id)
    return {"review_id": review_id, "repository_id": payments_repo["id"]}


async def test_findings_list_is_scoped_and_sorted(client, token, reviewed):
    response = await client.get("/api/v1/findings", headers=auth_headers(token))
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    ranks = [order[item["severity"]] for item in items]
    assert ranks == sorted(ranks)
    assert all(item["repository_full_name"] == "acme-corp/payments-api" for item in items)
    assert all(item["pull_request_number"] == 142 for item in items)


async def test_findings_are_invisible_to_other_users(client, other_token, reviewed):
    response = await client.get("/api/v1/findings", headers=auth_headers(other_token))
    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_findings_filters(client, token, reviewed):
    all_findings = await client.get("/api/v1/findings", headers=auth_headers(token))
    severity = all_findings.json()["items"][0]["severity"]

    filtered = await client.get(
        f"/api/v1/findings?severity={severity}", headers=auth_headers(token)
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] >= 1
    assert all(item["severity"] == severity for item in filtered.json()["items"])

    by_repo = await client.get(
        f"/api/v1/findings?repository_id={reviewed['repository_id']}",
        headers=auth_headers(token),
    )
    assert by_repo.json()["total"] == all_findings.json()["total"]

    unmatched = await client.get(
        "/api/v1/findings?search=zzzz-no-such-thing", headers=auth_headers(token)
    )
    assert unmatched.json()["total"] == 0


async def test_finding_status_transition_is_recorded(client, token, reviewed):
    listing = await client.get("/api/v1/findings", headers=auth_headers(token))
    finding_id = listing.json()["items"][0]["id"]

    response = await client.patch(
        f"/api/v1/findings/{finding_id}/status",
        json={"status": "dismissed", "note": "Intentional in this context."},
        headers=auth_headers(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "dismissed"
    assert body["resolution_note"] == "Intentional in this context."
    assert body["resolved_at"] is not None


async def test_finding_status_rejects_unknown_value(client, token, reviewed):
    listing = await client.get("/api/v1/findings", headers=auth_headers(token))
    finding_id = listing.json()["items"][0]["id"]
    response = await client.patch(
        f"/api/v1/findings/{finding_id}/status",
        json={"status": "maybe-later"},
        headers=auth_headers(token),
    )
    assert response.status_code == 422


async def test_other_user_cannot_change_finding_status(client, other_token, reviewed, token):
    listing = await client.get("/api/v1/findings", headers=auth_headers(token))
    finding_id = listing.json()["items"][0]["id"]
    response = await client.patch(
        f"/api/v1/findings/{finding_id}/status",
        json={"status": "resolved"},
        headers=auth_headers(other_token),
    )
    assert response.status_code == 404


async def test_analytics_reflects_the_completed_review(client, token, reviewed):
    response = await client.get("/api/v1/analytics", headers=auth_headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    overview = body["overview"]

    assert overview["repositories"] >= 1
    assert overview["reviews_total"] == 1
    assert overview["reviews_completed"] == 1
    assert overview["findings_total"] > 0
    assert overview["findings_open"] == overview["findings_total"]
    assert overview["avg_risk_score"] > 0
    assert overview["demo_mode"] is True

    assert body["severity_breakdown"]
    assert sum(point["count"] for point in body["severity_breakdown"]) == overview[
        "findings_total"
    ]
    assert body["category_breakdown"]
    assert body["top_repositories"][0]["label"] == "acme-corp/payments-api"
    assert body["trend"]


async def test_analytics_is_empty_for_an_unconnected_user(client, other_token):
    response = await client.get("/api/v1/analytics", headers=auth_headers(other_token))
    assert response.status_code == 200
    assert response.json()["overview"]["reviews_total"] == 0


async def test_queue_stats(client, token, reviewed):
    response = await client.get("/api/v1/analytics/queue", headers=auth_headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["completed_last_24h"] == 1
    assert body["queued"] == 0
    assert body["eager_mode"] is True


async def test_dismissed_findings_move_out_of_open_counts(client, token, reviewed):
    listing = await client.get("/api/v1/findings", headers=auth_headers(token))
    total = listing.json()["total"]
    finding_id = listing.json()["items"][0]["id"]

    await client.patch(
        f"/api/v1/findings/{finding_id}/status",
        json={"status": "resolved"},
        headers=auth_headers(token),
    )
    analytics = await client.get("/api/v1/analytics", headers=auth_headers(token))
    overview = analytics.json()["overview"]
    assert overview["findings_open"] == total - 1
    assert overview["findings_resolved"] == 1
