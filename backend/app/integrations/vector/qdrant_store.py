"""Qdrant-backed vector store (production default in Docker Compose).

Qdrant was chosen over ChromaDB because it runs as a standalone service with a
small, stable HTTP/gRPC client, supports server-side payload filtering (which is
required to scope every query to a single repository *and* commit SHA), and does
not pull a heavyweight embedded runtime into the backend image.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.core.logging import get_logger
from app.integrations.vector.base import CodeChunk, SearchResult, VectorStore

logger = get_logger(__name__)


class QdrantVectorStore(VectorStore):
    name = "qdrant"

    def __init__(
        self,
        url: str,
        collection: str,
        api_key: str | None = None,
    ) -> None:
        from qdrant_client import AsyncQdrantClient

        self.collection = collection
        self._client = AsyncQdrantClient(url=url, api_key=api_key, prefer_grpc=False)
        self._ready = False
        self._lock = asyncio.Lock()

    async def ensure_ready(self, dimension: int) -> None:
        from qdrant_client import models

        async with self._lock:
            if self._ready:
                return
            existing = await self._client.collection_exists(self.collection)
            if not existing:
                await self._client.create_collection(
                    collection_name=self.collection,
                    vectors_config=models.VectorParams(
                        size=dimension, distance=models.Distance.COSINE
                    ),
                )
                for field_name in ("repository_id", "commit_sha", "file_path", "kind"):
                    await self._client.create_payload_index(
                        collection_name=self.collection,
                        field_name=field_name,
                        field_schema=models.PayloadSchemaType.KEYWORD,
                    )
                logger.info("qdrant_collection_created", collection=self.collection)
            self._ready = True

    # ---------------------------------------------------------------- write
    async def upsert(self, chunks: list[CodeChunk], vectors: list[list[float]]) -> int:
        from qdrant_client import models

        if not chunks:
            return 0
        points = [
            models.PointStruct(
                id=_point_id(chunk.chunk_id),
                vector=vector,
                payload={"chunk_id": chunk.chunk_id, **chunk.payload()},
            )
            for chunk, vector in zip(chunks, vectors, strict=False)
        ]
        await self._client.upsert(collection_name=self.collection, points=points, wait=True)
        return len(points)

    async def delete_file(self, repository_id: str, file_path: str) -> int:
        from qdrant_client import models

        count = await self._count_where(
            [("repository_id", repository_id), ("file_path", file_path)]
        )
        await self._client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=_filter([("repository_id", repository_id), ("file_path", file_path)])
            ),
            wait=True,
        )
        return count

    async def delete_repository(self, repository_id: str) -> int:
        from qdrant_client import models

        count = await self._count_where([("repository_id", repository_id)])
        await self._client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=_filter([("repository_id", repository_id)])
            ),
            wait=True,
        )
        return count

    # ----------------------------------------------------------------- read
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
        from qdrant_client import models

        conditions: list[tuple[str, Any]] = [("repository_id", repository_id)]
        if commit_sha:
            conditions.append(("commit_sha", commit_sha))

        query_filter = _filter(conditions)
        if kinds:
            query_filter.must.append(  # type: ignore[union-attr]
                models.FieldCondition(key="kind", match=models.MatchAny(any=list(kinds)))
            )
        if exclude_files:
            query_filter.must_not = [
                models.FieldCondition(key="file_path", match=models.MatchValue(value=path))
                for path in exclude_files
            ]

        response = await self._client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        results: list[SearchResult] = []
        for point in response.points:
            payload = dict(point.payload or {})
            chunk_id = payload.pop("chunk_id", str(point.id))
            payload.setdefault("metadata", {})
            results.append(
                SearchResult(
                    chunk=CodeChunk(chunk_id=chunk_id, **payload),
                    score=float(point.score or 0.0),
                )
            )
        return results

    async def count(self, repository_id: str | None = None) -> int:
        if repository_id is None:
            info = await self._client.count(collection_name=self.collection, exact=True)
            return int(info.count)
        return await self._count_where([("repository_id", repository_id)])

    async def _count_where(self, conditions: list[tuple[str, Any]]) -> int:
        info = await self._client.count(
            collection_name=self.collection, count_filter=_filter(conditions), exact=True
        )
        return int(info.count)

    async def close(self) -> None:
        await self._client.close()


def _filter(conditions: list[tuple[str, Any]]) -> Any:
    from qdrant_client import models

    return models.Filter(
        must=[
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in conditions
        ]
    )


def _point_id(chunk_id: str) -> str:
    """Qdrant point IDs must be UUIDs or unsigned ints."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))
