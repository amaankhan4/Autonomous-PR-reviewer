"""Repository indexing: chunk -> embed -> upsert, incrementally.

Why incremental matters
-----------------------
A full re-index of a repository on every push is the single easiest way to make
this product too slow and too expensive to run. We store a content hash per
indexed file (:class:`~app.models.repository.IndexedFile`), so a push that
touches three files re-embeds three files -- not three thousand.

The service is deliberately usable in two modes:

* **DB-backed** (production/worker): incremental state persisted in Postgres.
* **Stateless** (tests, demo seeding): pass ``session=None`` and every file is
  indexed, which keeps the demo path free of database coupling.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.vector.base import CodeChunk, VectorStore
from app.integrations.vector.embeddings import EmbeddingProvider
from app.models.repository import IndexedFile
from app.services.chunking import chunk_file, content_hash, should_index

logger = get_logger(__name__)

FileReader = Callable[[str], Awaitable[str | None]]


@dataclass(slots=True)
class IndexReport:
    files_indexed: int = 0
    files_skipped: int = 0
    files_unchanged: int = 0
    files_deleted: int = 0
    chunks_indexed: int = 0
    chunks_deleted: int = 0
    incremental: bool = False
    duration_ms: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_indexed": self.files_indexed,
            "files_skipped": self.files_skipped,
            "files_unchanged": self.files_unchanged,
            "files_deleted": self.files_deleted,
            "chunks_indexed": self.chunks_indexed,
            "chunks_deleted": self.chunks_deleted,
            "incremental": self.incremental,
            "duration_ms": self.duration_ms,
            "skip_reasons": self.skip_reasons,
            "errors": self.errors[:10],
        }


class IndexingService:
    def __init__(
        self,
        vector_store: VectorStore,
        embedder: EmbeddingProvider,
        *,
        session: AsyncSession | None = None,
        batch_size: int = 64,
    ) -> None:
        self.vector_store = vector_store
        self.embedder = embedder
        self.session = session
        self.batch_size = batch_size

    async def index_repository(
        self,
        *,
        repository_id: str,
        commit_sha: str,
        files: Sequence[str],
        read_file: FileReader,
        excluded_paths: Iterable[str] = (),
        incremental: bool = True,
        deleted_files: Sequence[str] = (),
    ) -> IndexReport:
        started = time.perf_counter()
        report = IndexReport(incremental=incremental)
        await self.vector_store.ensure_ready(settings.EMBEDDING_DIM)

        known: dict[str, IndexedFile] = {}
        if incremental and self.session is not None:
            rows = await self.session.execute(
                select(IndexedFile).where(IndexedFile.repository_id == repository_id)
            )
            known = {row.file_path: row for row in rows.scalars().all()}

        for path in deleted_files:
            removed = await self.vector_store.delete_file(repository_id, path)
            report.chunks_deleted += removed
            report.files_deleted += 1
            if self.session is not None and path in known:
                await self.session.delete(known[path])

        pending: list[CodeChunk] = []
        for path in files:
            try:
                content = await read_file(path)
            except Exception as exc:
                report.errors.append(f"{path}: {type(exc).__name__}: {exc}")
                report.files_skipped += 1
                continue
            if content is None:
                report.files_skipped += 1
                report.skip_reasons["unreadable"] = report.skip_reasons.get("unreadable", 0) + 1
                continue

            ok, reason = should_index(path, len(content.encode("utf-8")), extra_excludes=excluded_paths)
            if not ok:
                report.files_skipped += 1
                report.skip_reasons[reason] = report.skip_reasons.get(reason, 0) + 1
                continue

            digest = content_hash(content)
            existing = known.get(path)
            if incremental and existing is not None and existing.content_hash == digest:
                report.files_unchanged += 1
                continue

            chunks = chunk_file(
                repository_id=repository_id,
                commit_sha=commit_sha,
                file_path=path,
                content=content,
            )
            if not chunks:
                report.files_skipped += 1
                report.skip_reasons["no_chunks"] = report.skip_reasons.get("no_chunks", 0) + 1
                continue

            # Replace previous chunks for this file so stale code cannot be retrieved.
            if existing is not None or not incremental:
                report.chunks_deleted += await self.vector_store.delete_file(repository_id, path)

            pending.extend(chunks)
            report.files_indexed += 1
            await self._record_file(
                repository_id=repository_id,
                path=path,
                digest=digest,
                commit_sha=commit_sha,
                language=chunks[0].language,
                chunk_count=len(chunks),
                existing=existing,
            )

            if len(pending) >= self.batch_size:
                report.chunks_indexed += await self._flush(pending)
                pending = []

        if pending:
            report.chunks_indexed += await self._flush(pending)

        if self.session is not None:
            await self.session.flush()

        report.duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info("index.completed", repository_id=repository_id, **report.to_dict())
        return report

    async def _flush(self, chunks: list[CodeChunk]) -> int:
        vectors = await self.embedder.embed([c.content for c in chunks])
        return await self.vector_store.upsert(chunks, vectors)

    async def _record_file(
        self,
        *,
        repository_id: str,
        path: str,
        digest: str,
        commit_sha: str,
        language: str,
        chunk_count: int,
        existing: IndexedFile | None,
    ) -> None:
        if self.session is None:
            return
        if existing is not None:
            existing.content_hash = digest
            existing.commit_sha = commit_sha
            existing.language = language
            existing.chunk_count = chunk_count
            return
        self.session.add(
            IndexedFile(
                repository_id=repository_id,
                file_path=path,
                content_hash=digest,
                commit_sha=commit_sha,
                language=language,
                chunk_count=chunk_count,
            )
        )

    async def clear_repository(self, repository_id: str) -> int:
        removed = await self.vector_store.delete_repository(repository_id)
        if self.session is not None:
            await self.session.execute(
                delete(IndexedFile).where(IndexedFile.repository_id == repository_id)
            )
        return removed
