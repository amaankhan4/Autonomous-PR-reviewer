"""Real GitHub App client.

Authentication model
--------------------
1. The App signs a short-lived (10 min) **RS256 JWT** with its private key.
2. That JWT is exchanged for an **installation access token** scoped to a single
   installation. Tokens live ~1 hour and are cached in memory only -- never
   persisted to the database and never returned to the frontend.
3. All repository calls use the installation token.

Only least-privilege permissions are requested (metadata/contents/pull_requests
read, pull-request reviews write). Administration scopes are never requested.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

from app.core.config import settings
from app.core.errors import GitHubError
from app.core.logging import get_logger
from app.integrations.github.base import (
    ChangedFile,
    GitHubProvider,
    InlineComment,
    InstallationInfo,
    PublishedReview,
    PullRequestInfo,
    RepositoryInfo,
)

logger = get_logger(__name__)

_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"


class _TokenCache:
    """In-memory installation-token cache with expiry margin."""

    def __init__(self, margin_seconds: int = 120) -> None:
        self._tokens: dict[int, tuple[str, float]] = {}
        self._margin = margin_seconds
        self._lock = asyncio.Lock()

    def get(self, installation_id: int) -> str | None:
        entry = self._tokens.get(installation_id)
        if entry is None:
            return None
        token, expires_at = entry
        if time.time() >= expires_at - self._margin:
            self._tokens.pop(installation_id, None)
            return None
        return token

    def set(self, installation_id: int, token: str, expires_at: float) -> None:
        self._tokens[installation_id] = (token, expires_at)

    def invalidate(self, installation_id: int) -> None:
        self._tokens.pop(installation_id, None)

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock


class GitHubAppProvider(GitHubProvider):
    name = "github-app"
    is_mock = False

    def __init__(
        self,
        *,
        app_id: str | None = None,
        private_key: str | None = None,
        api_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id or settings.GITHUB_APP_ID
        self.private_key = private_key or settings.github_private_key()
        self.api_url = (api_url or settings.GITHUB_API_URL).rstrip("/")
        self._client = client
        self._tokens = _TokenCache()

        if not self.app_id or not self.private_key:
            raise GitHubError(
                "GitHub App credentials are not configured. Set GITHUB_APP_ID and "
                "GITHUB_APP_PRIVATE_KEY, or enable MOCK_GITHUB=true for demo mode.",
                code="GITHUB_NOT_CONFIGURED",
                status_code=503,
            )

    # ------------------------------------------------------------ transport
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.api_url,
                timeout=settings.GITHUB_TIMEOUT_SECONDS,
                headers={"X-GitHub-Api-Version": _API_VERSION},
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _app_jwt(self) -> str:
        now = datetime.now(UTC)
        payload = {
            "iat": int((now - timedelta(seconds=30)).timestamp()),
            "exp": int((now + timedelta(minutes=9)).timestamp()),
            "iss": self.app_id,
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def _installation_token(self, installation_id: int) -> str:
        cached = self._tokens.get(installation_id)
        if cached:
            return cached
        async with self._tokens.lock:
            cached = self._tokens.get(installation_id)
            if cached:
                return cached
            response = await self.client.post(
                f"/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {self._app_jwt()}",
                    "Accept": _ACCEPT,
                },
            )
            if response.status_code >= 400:
                raise GitHubError(
                    f"Could not mint an installation token (HTTP {response.status_code})",
                    code="GITHUB_TOKEN_ERROR",
                )
            payload = response.json()
            token = payload["token"]
            expires_at = datetime.fromisoformat(
                payload["expires_at"].replace("Z", "+00:00")
            ).timestamp()
            self._tokens.set(installation_id, token, expires_at)
            return token

    async def _request(
        self,
        method: str,
        path: str,
        installation_id: int,
        *,
        accept: str = _ACCEPT,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retry_on_auth: bool = True,
    ) -> httpx.Response:
        token = await self._installation_token(installation_id)
        response = await self.client.request(
            method,
            path,
            params=params,
            json=json_body,
            headers={"Authorization": f"Bearer {token}", "Accept": accept},
        )
        if response.status_code == 401 and retry_on_auth:
            self._tokens.invalidate(installation_id)
            return await self._request(
                method,
                path,
                installation_id,
                accept=accept,
                params=params,
                json_body=json_body,
                retry_on_auth=False,
            )
        if response.status_code == 403 and "rate limit" in response.text.lower():
            reset = response.headers.get("x-ratelimit-reset")
            raise GitHubError(
                "GitHub API rate limit exceeded",
                code="GITHUB_RATE_LIMITED",
                status_code=429,
                details={"reset": reset},
            )
        if response.status_code >= 400:
            logger.warning(
                "github_api_error",
                method=method,
                path=path,
                status=response.status_code,
            )
            raise GitHubError(
                f"GitHub API {method} {path} failed with HTTP {response.status_code}",
                details={"status": response.status_code},
            )
        return response

    async def _paginate(
        self, path: str, installation_id: int, *, key: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while len(items) < limit:
            response = await self._request(
                "GET", path, installation_id, params={"per_page": 100, "page": page}
            )
            payload = response.json()
            batch = payload.get(key, []) if key else payload
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return items[:limit]

    # ------------------------------------------------------------- read api
    async def get_installation(self, installation_id: int) -> InstallationInfo:
        response = await self.client.get(
            f"/app/installations/{installation_id}",
            headers={"Authorization": f"Bearer {self._app_jwt()}", "Accept": _ACCEPT},
        )
        if response.status_code >= 400:
            raise GitHubError(f"Installation {installation_id} is not accessible")
        payload = response.json()
        account = payload.get("account") or {}
        return InstallationInfo(
            installation_id=int(payload["id"]),
            account_login=account.get("login") or "unknown",
            account_id=account.get("id"),
            account_type=account.get("type") or "User",
            avatar_url=account.get("avatar_url"),
            permissions=payload.get("permissions") or {},
        )

    async def list_installation_repositories(
        self, installation_id: int
    ) -> list[RepositoryInfo]:
        items = await self._paginate(
            "/installation/repositories", installation_id, key="repositories"
        )
        return [RepositoryInfo.from_api(item) for item in items]

    async def get_repository(self, installation_id: int, full_name: str) -> RepositoryInfo:
        response = await self._request("GET", f"/repos/{full_name}", installation_id)
        return RepositoryInfo.from_api(response.json())

    async def get_pull_request(
        self, installation_id: int, full_name: str, number: int
    ) -> PullRequestInfo:
        response = await self._request("GET", f"/repos/{full_name}/pulls/{number}", installation_id)
        return PullRequestInfo.from_api(response.json())

    async def get_pull_request_diff(
        self, installation_id: int, full_name: str, number: int
    ) -> str:
        response = await self._request(
            "GET",
            f"/repos/{full_name}/pulls/{number}",
            installation_id,
            accept="application/vnd.github.v3.diff",
        )
        return response.text

    async def get_changed_files(
        self, installation_id: int, full_name: str, number: int
    ) -> list[ChangedFile]:
        items = await self._paginate(f"/repos/{full_name}/pulls/{number}/files", installation_id)
        return [
            ChangedFile(
                filename=item.get("filename", ""),
                status=item.get("status", "modified"),
                additions=int(item.get("additions") or 0),
                deletions=int(item.get("deletions") or 0),
                changes=int(item.get("changes") or 0),
                patch=item.get("patch"),
                previous_filename=item.get("previous_filename"),
                sha=item.get("sha"),
            )
            for item in items
        ]

    async def get_file(
        self, installation_id: int, full_name: str, path: str, ref: str
    ) -> str | None:
        try:
            response = await self._request(
                "GET",
                f"/repos/{full_name}/contents/{path}",
                installation_id,
                accept="application/vnd.github.raw",
                params={"ref": ref},
            )
        except GitHubError:
            return None
        if len(response.content) > settings.MAX_FILE_BYTES:
            return None
        return response.text

    async def list_files(
        self, installation_id: int, full_name: str, ref: str
    ) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            f"/repos/{full_name}/git/trees/{ref}",
            installation_id,
            params={"recursive": "1"},
        )
        payload = response.json()
        return [
            {"path": entry["path"], "size": entry.get("size", 0), "sha": entry.get("sha")}
            for entry in payload.get("tree", [])
            if entry.get("type") == "blob"
        ]

    # ------------------------------------------------------------ write api
    async def create_review(
        self,
        installation_id: int,
        full_name: str,
        number: int,
        *,
        commit_sha: str,
        body: str,
        event: str = "COMMENT",
        comments: list[InlineComment] | None = None,
    ) -> PublishedReview:
        payload: dict[str, Any] = {"commit_id": commit_sha, "body": body, "event": event}
        inline = comments or []
        if inline:
            payload["comments"] = [comment.to_api() for comment in inline]

        try:
            response = await self._request(
                "POST", f"/repos/{full_name}/pulls/{number}/reviews", installation_id,
                json_body=payload,
            )
        except GitHubError:
            # GitHub rejects the whole review if a single comment anchors to a
            # line outside the diff. Fall back to a summary-only review so the
            # analysis is never lost.
            if not inline:
                raise
            logger.warning("github_review_inline_rejected", repo=full_name, pr=number)
            response = await self._request(
                "POST",
                f"/repos/{full_name}/pulls/{number}/reviews",
                installation_id,
                json_body={"commit_id": commit_sha, "body": body, "event": event},
            )
            data = response.json()
            return PublishedReview(
                review_id=data.get("id"),
                html_url=data.get("html_url"),
                submitted_comments=0,
                rejected_comments=[c.to_api() for c in inline],
            )

        data = response.json()
        return PublishedReview(
            review_id=data.get("id"),
            html_url=data.get("html_url"),
            submitted_comments=len(inline),
        )

    async def create_inline_comment(
        self,
        installation_id: int,
        full_name: str,
        number: int,
        *,
        commit_sha: str,
        comment: InlineComment,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            f"/repos/{full_name}/pulls/{number}/comments",
            installation_id,
            json_body={"commit_id": commit_sha, **comment.to_api()},
        )
        return response.json()
