"""End-to-end review pipeline against the demo fixtures."""

from __future__ import annotations

import pytest

from app.core.enums import ReviewStatus
from app.demo import fixtures
from app.integrations.github.mock_provider import MockGitHubProvider
from app.workers.pipeline import execute_review
from tests.conftest import auth_headers


@pytest.fixture
async def imported_review(client, token, payments_repo, dispatched) -> dict:
    response = await client.post(
        "/api/v1/pull-requests/import",
        json={"repository_id": payments_repo["id"], "number": 142, "review": True},
        headers=auth_headers(token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created"] is True
    assert ("review", (body["review_run_id"],)) in dispatched
    return body


async def test_import_creates_pull_request_and_queues_review(
    client, token, imported_review
):
    listing = await client.get("/api/v1/pull-requests", headers=auth_headers(token))
    assert listing.status_code == 200, listing.text
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["github_pr_number"] == 142
    assert items[0]["latest_review_status"] == ReviewStatus.QUEUED.value


async def test_full_pipeline_produces_validated_findings(
    client, token, imported_review, payments_repo
):
    review_id = imported_review["review_run_id"]

    result = await execute_review(review_id)
    assert result["status"] == ReviewStatus.COMPLETED.value, result

    detail = await client.get(f"/api/v1/reviews/{review_id}", headers=auth_headers(token))
    assert detail.status_code == 200, detail.text
    body = detail.json()

    assert body["status"] == "completed"
    assert body["progress"] == 100
    assert body["summary"]
    assert body["findings_count"] == len(body["findings"])
    assert body["findings"], "the demo PR contains real bugs and must produce findings"
    assert body["risk_score"] > 0
    assert body["risk_band"] in {"low", "moderate", "elevated", "high", "critical"}

    # Every finding must point at a file that is actually in the diff.
    changed = {
        entry["filename"]
        for entry in fixtures.changed_files_for(
            fixtures.find_repository("acme-corp/payments-api"),
            fixtures.find_pull_request("acme-corp/payments-api", 142),
        )
    }
    for finding in body["findings"]:
        assert finding["file_path"] in changed
        assert 0.0 <= finding["confidence"] <= 1.0
        assert finding["severity"] in {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}

    # Findings are ordered by severity, most serious first.
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    ranks = [order[f["severity"]] for f in body["findings"]]
    assert ranks == sorted(ranks)


async def test_pipeline_publishes_to_github(client, token, imported_review):
    await execute_review(imported_review["review_run_id"])

    assert MockGitHubProvider.published_reviews, "expected a review to be published"
    published = MockGitHubProvider.published_reviews[-1]
    assert published["repository"] == "acme-corp/payments-api"
    assert published["pr_number"] == 142
    assert published["event"] == "COMMENT"
    assert "Autonomous PR Reviewer" in published["body"] or published["body"]

    # Inline comments must anchor to lines GitHub accepted.
    assert not published["rejected"], published["rejected"]

    detail = await client.get(
        f"/api/v1/reviews/{imported_review['review_run_id']}", headers=auth_headers(token)
    )
    body = detail.json()
    assert body["published"] is True
    assert body["github_review_url"]
    assert any(comment["kind"] == "summary" for comment in body["comments"])


async def test_review_records_llm_usage(client, token, imported_review):
    await execute_review(imported_review["review_run_id"])
    detail = await client.get(
        f"/api/v1/reviews/{imported_review['review_run_id']}", headers=auth_headers(token)
    )
    usage = detail.json()["llm_usage"]
    assert usage, "LLM calls must be accounted for"
    assert all(item["provider"] for item in usage)
    assert all(item["estimated_cost_usd"] >= 0 for item in usage)


async def test_review_diff_attaches_findings_to_files(client, token, imported_review):
    await execute_review(imported_review["review_run_id"])
    response = await client.get(
        f"/api/v1/reviews/{imported_review['review_run_id']}/diff",
        headers=auth_headers(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["files"]
    for entry in body["files"]:
        assert entry["patch"]
        for finding in entry["findings"]:
            assert finding["file_path"] == entry["filename"]


async def test_repository_is_indexed_during_the_first_review(
    client, token, imported_review, payments_repo
):
    await execute_review(imported_review["review_run_id"])
    status = await client.get(
        f"/api/v1/repositories/{payments_repo['id']}/index-status",
        headers=auth_headers(token),
    )
    body = status.json()
    assert body["status"] == "completed"
    assert body["indexed_files"] > 0
    assert body["indexed_chunks"] > 0


async def test_second_review_of_same_commit_is_idempotent(
    client, token, payments_repo, dispatched
):
    first = await client.post(
        "/api/v1/pull-requests/import",
        json={"repository_id": payments_repo["id"], "number": 142},
        headers=auth_headers(token),
    )
    second = await client.post(
        "/api/v1/pull-requests/import",
        json={"repository_id": payments_repo["id"], "number": 142},
        headers=auth_headers(token),
    )
    assert first.json()["review_run_id"] == second.json()["review_run_id"]
    assert second.json()["created"] is False
    assert len([call for call in dispatched if call[0] == "review"]) == 1


async def test_force_rerun_reuses_the_row_and_clears_findings(
    client, token, imported_review
):
    review_id = imported_review["review_run_id"]
    await execute_review(review_id)

    detail = await client.get(f"/api/v1/reviews/{review_id}", headers=auth_headers(token))
    pull_request_id = detail.json()["pull_request_id"]

    rerun = await client.post(
        "/api/v1/reviews",
        json={"pull_request_id": pull_request_id, "force": True},
        headers=auth_headers(token),
    )
    assert rerun.status_code == 202, rerun.text
    assert rerun.json()["review_run_id"] == review_id
    assert rerun.json()["created"] is True

    progress = await client.get(
        f"/api/v1/reviews/{review_id}/progress", headers=auth_headers(token)
    )
    assert progress.json()["status"] == ReviewStatus.QUEUED.value
    assert progress.json()["findings_count"] == 0


async def test_trigger_without_target_is_422(client, token):
    response = await client.post(
        "/api/v1/reviews", json={"force": True}, headers=auth_headers(token)
    )
    assert response.status_code == 422


async def test_review_is_not_visible_to_other_users(
    client, other_token, imported_review
):
    response = await client.get(
        f"/api/v1/reviews/{imported_review['review_run_id']}",
        headers=auth_headers(other_token),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REVIEW_NOT_FOUND"
