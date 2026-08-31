"""In-memory vector store with optional JSON persistence.

Used for local development, the test-suite and demo mode so the product runs
without an external vector database. Exposes exactly the same interface as the
Qdrant store, so switching is a single environment variable.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.integrations.vector.base import CodeChunk, SearchResult, VectorStore
from app.integrations.vector.embeddings import cosine_similarity

logger = get_logger(__name__)


class InMemoryVectorStore(VectorStore):
    name = "memory"

    def __init__(self, persist_path: str | None = None) -> None:
        self._vectors: dict[str, list[float]] = {}
        self._chunks: dict[str, CodeChunk] = {}
        self._lock = asyncio.Lock()
        self._persist_path = Path(persist_path) if persist_path else None
        self._dimension: int | None = None
        if self._persist_path is not None:
            self._load()

    # ------------------------------------------------------------ lifecycle
    async def ensure_ready(self, dimension: int) -> None:
        if self._dimension is not None and self._dimension != dimension:
            logger.warning(
                "vector_dimension_changed",
                previous=self._dimension,
                current=dimension,
            )
            self._vectors.clear()
            self._chunks.clear()
        self._dimension = dimension

    def _load(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            payload = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):  # pragma: no cover - defensive
            logger.warning("vector_store_load_failed", path=str(self._persist_path))
            return
        for item in payload.get("chunks", []):
            chunk = CodeChunk(**item["chunk"])
            self._chunks[chunk.chunk_id] = chunk
            self._vectors[chunk.chunk_id] = item["vector"]
        self._dimension = payload.get("dimension")

    def _save(self) -> None:
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "dimension": self._dimension,
                "chunks": [
                    {
                        "chunk": {
                            "chunk_id": chunk.chunk_id,
                            "repository_id": chunk.repository_id,
                            "commit_sha": chunk.commit_sha,
                            "file_path": chunk.file_path,
                            "language": chunk.language,
                            "symbol": chunk.symbol,
                            "kind": chunk.kind,
                            "start_line": chunk.start_line,
                            "end_line": chunk.end_line,
                            "content": chunk.content,
                            "content_hash": chunk.content_hash,
                            "metadata": chunk.metadata,
                        },
                        "vector": self._vectors[chunk_id],
                    }
                    for chunk_id, chunk in self._chunks.items()
                ],
            }
            self._persist_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:  # pragma: no cover - defensive
            logger.warning("vector_store_persist_failed", path=str(self._persist_path))

    # ---------------------------------------------------------------- write
    async def upsert(self, chunks: list[CodeChunk], vectors: list[list[float]]) -> int:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")
        async with self._lock:
            for chunk, vector in zip(chunks, vectors, strict=False):
                self._chunks[chunk.chunk_id] = chunk
                self._vectors[chunk.chunk_id] = vector
            self._save()
        return len(chunks)

    async def delete_file(self, repository_id: str, file_path: str) -> int:
        async with self._lock:
            targets = [
                chunk_id
                for chunk_id, chunk in self._chunks.items()
                if chunk.repository_id == repository_id and chunk.file_path == file_path
            ]
            for chunk_id in targets:
                self._chunks.pop(chunk_id, None)
                self._vectors.pop(chunk_id, None)
            if targets:
                self._save()
            return len(targets)

    async def delete_repository(self, repository_id: str) -> int:
        async with self._lock:
            targets = [
                chunk_id
                for chunk_id, chunk in self._chunks.items()
                if chunk.repository_id == repository_id
            ]
            for chunk_id in targets:
                self._chunks.pop(chunk_id, None)
                self._vectors.pop(chunk_id, None)
            if targets:
                self._save()
            return len(targets)

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
        excluded = set(exclude_files or [])
        kind_filter = set(kinds or [])
        scored: list[SearchResult] = []
        for chunk_id, chunk in self._chunks.items():
            if chunk.repository_id != repository_id:
                continue
            if commit_sha and chunk.commit_sha != commit_sha:
                continue
            if kind_filter and chunk.kind not in kind_filter:
                continue
            if chunk.file_path in excluded:
                continue
            score = cosine_similarity(vector, self._vectors[chunk_id])
            if score <= 0:
                continue
            scored.append(SearchResult(chunk=chunk, score=score))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    async def count(self, repository_id: str | None = None) -> int:
        if repository_id is None:
            return len(self._chunks)
        return sum(1 for c in self._chunks.values() if c.repository_id == repository_id)
