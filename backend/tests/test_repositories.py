"""Installation linking, repository listing and per-repository settings."""

from __future__ import annotations

from app.demo import fixtures
from tests.conftest import auth_headers


async def test_connect_installation_creates_repositories(client, token):
    response = await client.post(
        "/api/v1/installations/connect",
        json={"installation_id": fixtures.DEMO_INSTALLATION_ID},
        headers=auth_headers(token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["repositories_synced"] == len(fixtures.DEMO_REPOSITORIES)
    assert body["demo_mode"] is True


async def test_connect_is_idempotent(client, token):
    first = await client.post(
        "/api/v1/installations/connect", json={}, headers=auth_headers(token)
    )
    second = await client.post(
        "/api/v1/installations/connect", json={}, headers=auth_headers(token)
    )
    assert first.status_code == 201 and second.status_code == 201
    assert second.json()["repositories_added"] == 0

    repos = await client.get("/api/v1/repositories", headers=auth_headers(token))
    assert repos.json()["total"] == len(fixtures.DEMO_REPOSITORIES)


async def test_repositories_are_scoped_to_the_user(client, token, other_token, connected):
    mine = await client.get("/api/v1/repositories", headers=auth_headers(token))
    theirs = await client.get("/api/v1/repositories", headers=auth_headers(other_token))
    assert mine.json()["total"] > 0
    assert theirs.json()["total"] == 0


async def test_repository_detail_is_404_for_other_users(
    client, other_token, payments_repo
):
    response = await client.get(
        f"/api/v1/repositories/{payments_repo['id']}", headers=auth_headers(other_token)
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REPOSITORY_NOT_FOUND"


async def test_repository_detail_includes_defaults(client, token, payments_repo):
    response = await client.get(
        f"/api/v1/repositories/{payments_repo['id']}", headers=auth_headers(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["settings"]["auto_review_enabled"] is True
    assert body["settings"]["min_severity"] in {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert body["open_pull_requests"] >= 0


async def test_settings_partial_update(client, token, payments_repo):
    response = await client.patch(
        f"/api/v1/repositories/{payments_repo['id']}/settings",
        json={"min_severity": "high", "auto_review_enabled": False},
        headers=auth_headers(token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["min_severity"] == "HIGH"
    assert body["auto_review_enabled"] is False
    # Untouched fields keep their previous values.
    assert body["publish_to_github"] is True


async def test_settings_reject_unknown_analyzer(client, token, payments_repo):
    response = await client.patch(
        f"/api/v1/repositories/{payments_repo['id']}/settings",
        json={"enabled_analyzers": ["diff", "telepathy"]},
        headers=auth_headers(token),
    )
    assert response.status_code == 422


async def test_settings_reject_invalid_severity(client, token, payments_repo):
    response = await client.patch(
        f"/api/v1/repositories/{payments_repo['id']}/settings",
        json={"min_severity": "URGENT"},
        headers=auth_headers(token),
    )
    assert response.status_code == 422


async def test_reindex_enqueues_a_job(client, token, payments_repo, dispatched):
    response = await client.post(
        f"/api/v1/repositories/{payments_repo['id']}/reindex",
        json={"full": True},
        headers=auth_headers(token),
    )
    assert response.status_code == 200, response.text
    assert ("index", (payments_repo["id"], None, True)) in dispatched


async def test_index_status_starts_pending(client, token, payments_repo):
    response = await client.get(
        f"/api/v1/repositories/{payments_repo['id']}/index-status",
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["indexed_files"] == 0
