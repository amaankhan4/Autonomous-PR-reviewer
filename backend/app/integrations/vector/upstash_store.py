"""Upstash Vector store (serverless, no local infrastructure required).

Why a namespace per repository
------------------------------
Upstash applies metadata filters with a bounded "filtering budget" and falls
back to *post*-filtering once that budget is exhausted, which can silently
return fewer than ``topK`` matches. Repository scoping is the one predicate that
must never be approximate -- leaking another repository's code into a review
prompt would be a security problem -- so it is expressed structurally as a
namespace rather than as a filter.

That choice also makes two operations exact and cheap:
  * ``count(repository_id)`` reads the namespace vector count from ``/info``
  * ``delete_repository`` is a single ``delete-namespace`` call

Commit/kind/path predicates remain ordinary metadata filters, applied within an
already small candidate set.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.vector.base import CodeChunk, SearchResult, VectorStore

logger = get_logger(__name__)

_UNSAFE_NAMESPACE = re.compile(r"[^A-Za-z0-9_-]")


class UpstashVectorError(RuntimeError):
    """Raised when the Upstash Vector REST API rejects a request."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def is_missing_namespace(self) -> bool:
        """True when the failure is just an index/namespace that was never created.

        Upstash returns 404 with a prose message ("... does not exist"), so the
        status code is matched rather than the wording.
        """
        return self.status_code == 404


def _namespace(repository_id: str) -> str:
    """Namespaces are path segments, so restrict them to a safe alphabet."""
    return f"repo_{_UNSAFE_NAMESPACE.sub('_', repository_id)}"


def _quote(value: str) -> str:
    """Quote a literal for Upstash's SQL-like filter grammar."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _build_filter(
    commit_sha: str | None,
    kinds: list[str] | None,
    exclude_files: list[str] | None,
) -> str:
    clauses: list[str] = []
    if commit_sha:
        clauses.append(f"commit_sha = {_quote(commit_sha)}")
    if kinds:
        joined = ", ".join(_quote(kind) for kind in kinds)
        clauses.append(f"kind IN ({joined})")
    for path in exclude_files or []:
        clauses.append(f"file_path != {_quote(path)}")
    return " AND ".join(clauses)


class UpstashVectorStore(VectorStore):
    name = "upstash"

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: float | None = None,
        batch_size: int | None = None,
        max_content_chars: int | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout or settings.UPSTASH_VECTOR_TIMEOUT_SECONDS,
        )
        self._batch_size = batch_size or settings.UPSTASH_VECTOR_BATCH_SIZE
        self._max_content = max_content_chars or settings.UPSTASH_VECTOR_MAX_CONTENT_CHARS
        self._ready = False

    # -------------------------------------------------------------- plumbing
    async def _call(self, method: str, path: str, payload: Any = None) -> Any:
        try:
            response = await self._client.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise UpstashVectorError(f"Upstash Vector request failed: {exc}") from exc
        if response.status_code >= 400:
            raise UpstashVectorError(
                f"Upstash Vector {method} {path} -> {response.status_code}: "
                f"{response.text[:300]}",
                status_code=response.status_code,
            )
        body = response.json()
        if isinstance(body, dict) and "error" in body and body["error"]:
            raise UpstashVectorError(str(body["error"])[:300])
        return body.get("result") if isinstance(body, dict) else body

    async def ensure_ready(self, dimension: int) -> None:
        if self._ready:
            return
        info = await self._call("GET", "/info")
        actual = int(info.get("dimension") or 0)
        if actual and actual != dimension:
            raise UpstashVectorError(
                f"Upstash index dimension is {actual} but this deployment produces "
                f"{dimension}-dimensional embeddings. Recreate the index with "
                f"dimension {dimension}, or set EMBEDDING_DIM={actual}."
            )
        self._ready = True
        logger.info(
            "upstash_vector_ready",
            dimension=actual or dimension,
            vectors=info.get("vectorCount"),
            similarity=info.get("similarityFunction"),
        )

    # ----------------------------------------------------------------- write
    def _metadata(self, chunk: CodeChunk) -> dict[str, Any]:
        payload = chunk.payload()
        content = payload.get("content") or ""
        if len(content) > self._max_content:
            payload["content"] = content[: self._max_content]
            payload["content_truncated"] = True
        return payload

    async def upsert(self, chunks: list[CodeChunk], vectors: list[list[float]]) -> int:
        if not chunks:
            return 0

        by_namespace: dict[str, list[dict[str, Any]]] = {}
        for chunk, vector in zip(chunks, vectors, strict=False):
            by_namespace.setdefault(_namespace(chunk.repository_id), []).append(
                {
                    "id": chunk.chunk_id,
                    "vector": list(vector),
                    "metadata": self._metadata(chunk),
                }
            )

        written = 0
        for namespace, points in by_namespace.items():
            for start in range(0, len(points), self._batch_size):
                batch = points[start : start + self._batch_size]
                await self._call("POST", f"/upsert/{namespace}", batch)
                written += len(batch)
        return written

    async def delete_file(self, repository_id: str, file_path: str) -> int:
        try:
            result = await self._call(
                "DELETE",
                f"/delete/{_namespace(repository_id)}",
                {"filter": f"file_path = {_quote(file_path)}"},
            )
        except UpstashVectorError as exc:
            if exc.is_missing_namespace:
                return 0
            raise
        return int((result or {}).get("deleted", 0))

    async def delete_repository(self, repository_id: str) -> int:
        namespace = _namespace(repository_id)
        existing = await self.count(repository_id)
        try:
            await self._call("DELETE", f"/delete-namespace/{namespace}")
        except UpstashVectorError as exc:
            # Deleting a namespace that was never created is a no-op, not a failure.
            if exc.is_missing_namespace:
                return 0
            raise
        return existing

    # ------------------------------------------------------------------ read
    async def search(
        self,
        repository_id: str,
        vector: list[float],
        *,
        limit: int = 10,
        commit_sha: str | None = None,
        kinds: list[str] | None = None,
        exclude_files: list[str] | None = None,
    ) -> list[SearchResult]:
        payload: dict[str, Any] = {
            "vector": list(vector),
            "topK": limit,
            "includeMetadata": True,
            "includeVectors": False,
        }
        query_filter = _build_filter(commit_sha, kinds, exclude_files)
        if query_filter:
            payload["filter"] = query_filter

        try:
            matches = await self._call(
                "POST", f"/query/{_namespace(repository_id)}", payload
            )
        except UpstashVectorError as exc:
            # A repository that has never been indexed has no namespace yet;
            # that is an empty result, not a failure.
            if exc.is_missing_namespace:
                return []
            raise

        results: list[SearchResult] = []
        for match in matches or []:
            metadata = dict(match.get("metadata") or {})
            metadata.pop("content_truncated", None)
            chunk = _chunk_from_metadata(str(match.get("id")), metadata)
            if chunk is None:
                continue
            results.append(SearchResult(chunk=chunk, score=float(match.get("score") or 0.0)))
        return results

    async def count(self, repository_id: str | None = None) -> int:
        info = await self._call("GET", "/info")
        if repository_id is None:
            return int(info.get("vectorCount") or 0)
        namespaces = info.get("namespaces") or {}
        entry = namespaces.get(_namespace(repository_id)) or {}
        return int(entry.get("vectorCount") or 0)

    async def close(self) -> None:
        await self._client.aclose()


def _chunk_from_metadata(chunk_id: str, metadata: dict[str, Any]) -> CodeChunk | None:
    """Rebuild a CodeChunk, tolerating metadata written by an older version."""
    try:
        return CodeChunk(
            chunk_id=chunk_id,
            repository_id=str(metadata.get("repository_id", "")),
            commit_sha=str(metadata.get("commit_sha", "")),
            file_path=str(metadata.get("file_path", "")),
            language=str(metadata.get("language", "unknown")),
            symbol=str(metadata.get("symbol", "")),
            kind=str(metadata.get("kind", "module")),
            start_line=int(metadata.get("start_line") or 0),
            end_line=int(metadata.get("end_line") or 0),
            content=str(metadata.get("content", "")),
            content_hash=str(metadata.get("content_hash", "")),
            metadata=dict(metadata.get("metadata") or {}),
        )
    except (TypeError, ValueError):
        logger.warning("upstash_vector_malformed_metadata", chunk_id=chunk_id)
        return None
