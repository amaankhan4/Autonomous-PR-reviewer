"""Vector store abstraction.

The reviewer stores *semantic units* (functions, classes, docs sections, tests,
historical findings) rather than fixed-size token windows, and every vector is
tagged with the commit SHA it came from so code from different repository
versions can never be mixed.
"""

from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class CodeChunk:
    """One indexed semantic unit."""

    chunk_id: str
    repository_id: str
    commit_sha: str
    file_path: str
    language: str
    symbol: str
    kind: str  # function | class | method | doc | test | finding | module
    start_line: int
    end_line: int
    content: str
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("chunk_id", None)
        return data


@dataclass(slots=True)
class SearchResult:
    chunk: CodeChunk
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.chunk.file_path,
            "symbol": self.chunk.symbol,
            "kind": self.chunk.kind,
            "language": self.chunk.language,
            "start_line": self.chunk.start_line,
            "end_line": self.chunk.end_line,
            "score": round(self.score, 4),
            "content": self.chunk.content,
        }


class VectorStore(abc.ABC):
    """Interface implemented by the Qdrant and in-memory stores."""

    name: str = "vector-store"

    @abc.abstractmethod
    async def ensure_ready(self, dimension: int) -> None: ...

    @abc.abstractmethod
    async def upsert(self, chunks: list[CodeChunk], vectors: list[list[float]]) -> int: ...

    @abc.abstractmethod
    async def search(
        self,
        repository_id: str,
        vector: list[float],
        *,
        limit: int = 10,
        commit_sha: str | None = None,
        kinds: list[str] | None = None,
        exclude_files: list[str] | None = None,
    ) -> list[SearchResult]: ...

    @abc.abstractmethod
    async def delete_file(self, repository_id: str, file_path: str) -> int: ...

    @abc.abstractmethod
    async def delete_repository(self, repository_id: str) -> int: ...

    @abc.abstractmethod
    async def count(self, repository_id: str | None = None) -> int: ...

    async def close(self) -> None:  # pragma: no cover - default no-op
        return None
